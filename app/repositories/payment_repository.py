import builtins
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.orm.audit_log import AuditLogORM
from app.models.orm.payment import PaymentTransactionORM
from app.models.payment import PaymentTransaction, validate_transition
from app.models.sla import SLAResult


def _orm_to_pydantic(orm: PaymentTransactionORM) -> PaymentTransaction:
    return PaymentTransaction(
        id=orm.id,
        transaction_hash=orm.transaction_hash,
        type=orm.type,
        amount=orm.amount,
        asset_code=orm.asset_code,
        from_address=orm.from_address,
        to_address=orm.to_address,
        status=orm.status,
        outage_id=orm.outage_id,
        sla_result_id=orm.sla_result_id,
        created_at=orm.created_at,
        confirmed_at=orm.confirmed_at,
        retry_count=orm.retry_count,
        last_retried_at=orm.last_retried_at,
    )


class PaymentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: PaymentTransaction) -> PaymentTransaction:
        orm = PaymentTransactionORM(
            id=data.id,
            transaction_hash=data.transaction_hash,
            type=data.type,
            amount=data.amount,
            asset_code=data.asset_code,
            from_address=data.from_address,
            to_address=data.to_address,
            status=data.status,
            outage_id=data.outage_id,
            sla_result_id=data.sla_result_id,
            created_at=data.created_at,
            confirmed_at=data.confirmed_at,
        )
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def get(self, transaction_id: str) -> PaymentTransaction | None:
        orm = self.db.query(PaymentTransactionORM).filter(PaymentTransactionORM.id == transaction_id).first()
        if not orm:
            return None
        return _orm_to_pydantic(orm)

    def get_by_sla_result(self, sla_result_id: int, for_update: bool = False) -> PaymentTransaction | None:
        query = self.db.query(PaymentTransactionORM).filter(PaymentTransactionORM.sla_result_id == sla_result_id)
        if for_update:
            query = query.with_for_update()
        orm = query.first()
        if not orm:
            return None
        return _orm_to_pydantic(orm)

    # BE-286: whitelisted sort columns to avoid arbitrary-column ORDER BY.
    SORT_COLUMNS = {
        "created_at": PaymentTransactionORM.created_at,
        "amount": PaymentTransactionORM.amount,
        "status": PaymentTransactionORM.status,
    }

    def list(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        outage_id: str | None = None,
        type: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
    ) -> tuple[list[PaymentTransaction], int]:
        query = self.db.query(PaymentTransactionORM)

        if status:
            query = query.filter(PaymentTransactionORM.status == status)
        if outage_id:
            query = query.filter(PaymentTransactionORM.outage_id == outage_id)
        if type:
            query = query.filter(PaymentTransactionORM.type == type)
        if date_from:
            query = query.filter(PaymentTransactionORM.created_at >= date_from)
        if date_to:
            query = query.filter(PaymentTransactionORM.created_at <= date_to)

        sort_column = self.SORT_COLUMNS.get(sort_by, PaymentTransactionORM.created_at)
        order_clause = sort_column.asc() if sort_dir == "asc" else sort_column.desc()

        # BE-285: compute the total in the same statement as the page via
        # COUNT(*) OVER() instead of a separate query.count() scan, so the
        # total always matches the returned page under concurrent writes.
        rows = (
            query.add_columns(func.count().over())
            .order_by(order_clause)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        total = rows[0][1] if rows else 0
        return [_orm_to_pydantic(r[0]) for r in rows], total

    def list_by_outage(self, outage_id: str) -> builtins.list[PaymentTransaction]:
        rows = self.db.query(PaymentTransactionORM).filter(PaymentTransactionORM.outage_id == outage_id).all()
        return [_orm_to_pydantic(r) for r in rows]

    def update_status(self, transaction_id: str, status: str) -> PaymentTransaction | None:
        orm = self.db.query(PaymentTransactionORM).filter(PaymentTransactionORM.id == transaction_id).first()
        if not orm:
            return None
        orm.status = status
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def create_for_sla_result(self, outage_id: str, sla_result: SLAResult) -> PaymentTransaction:
        if sla_result.id is None:
            raise ValueError("SLA result id is required to generate a payment record")

        existing = self.get_by_sla_result(sla_result.id, for_update=True)
        if existing:
            return existing

        normalized_amount = abs(float(sla_result.amount))
        # BE-288: no real Stellar submission path exists yet, so this hash is
        # simulated. Use a genuinely random, unique value (instead of a
        # deterministic sla_result-derived string) so retries/duplicates
        # can't collide, and prefix it so it's never mistaken for a real
        # on-chain transaction hash.
        transaction = PaymentTransaction(
            id=f"pay_{uuid4().hex[:12]}",
            transaction_hash=f"simulated-{uuid4().hex}",
            type=sla_result.payment_type,
            amount=normalized_amount,
            asset_code=settings.PAYMENT_ASSET_CODE,
            from_address=settings.PAYMENT_FROM_ADDRESS,
            to_address=settings.PAYMENT_TO_ADDRESS,
            status="pending",
            outage_id=outage_id,
            sla_result_id=sla_result.id,
            created_at=datetime.now(UTC),
            confirmed_at=None,
        )
        return self.create(transaction)

    MAX_RETRIES = 3

    def reconcile(self, transaction_id: str, new_status: str) -> PaymentTransaction | None:
        """Refresh payment status and mark as auditable reconciliation."""
        orm = self.db.query(PaymentTransactionORM).filter(PaymentTransactionORM.id == transaction_id).first()
        if not orm:
            return None
        validate_transition(orm.status, new_status)
        orm.status = new_status
        if new_status == "confirmed":
            orm.confirmed_at = datetime.now(UTC)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def retry(self, transaction_id: str) -> PaymentTransaction | None:
        """Increment retry counter (bounded by MAX_RETRIES) and reset to pending."""
        orm = self.db.query(PaymentTransactionORM).filter(PaymentTransactionORM.id == transaction_id).first()
        if not orm:
            return None
        if orm.retry_count >= self.MAX_RETRIES:
            return None  # caller should raise 409
        validate_transition(orm.status, "pending")
        orm.retry_count += 1
        orm.last_retried_at = datetime.now(UTC)
        orm.status = "pending"
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    HISTORY_EVENT_TYPES = {"payment_reconciled", "payment_retried"}

    def get_payment_history(self, transaction_id: str) -> builtins.list[dict]:
        """Return audit log entries for reconcile/retry actions on a payment."""
        rows = (
            self.db.query(AuditLogORM)
            .filter(
                AuditLogORM.event_type.in_(self.HISTORY_EVENT_TYPES),
            )
            .order_by(AuditLogORM.created_at.asc())
            .all()
        )
        return [
            {
                "event_type": r.event_type,
                "actor": r.email,
                "details": r.details,
                "timestamp": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
            if r.details and r.details.get("id") == transaction_id
        ]

    def get_reconciliation_history(self, transaction_id: str) -> builtins.list[dict]:
        """Return detailed reconciliation history with actor context and status transitions.

        BE-027: Provides a structured view of who changed what and why, suitable for
        audit screens and frontend drawers.
        """
        rows = (
            self.db.query(AuditLogORM)
            .filter(
                AuditLogORM.event_type.in_(self.HISTORY_EVENT_TYPES),
            )
            .order_by(AuditLogORM.created_at.asc())
            .all()
        )

        history = []
        for r in rows:
            if r.details and r.details.get("id") == transaction_id:
                # Extract previous and new status from details
                previous_status = r.details.get("previous_status")
                new_status = r.details.get("status") or r.details.get("new_status")

                history.append(
                    {
                        "event_type": r.event_type,
                        "actor": r.email,
                        "previous_status": previous_status,
                        "new_status": new_status,
                        "timestamp": r.created_at.isoformat() if r.created_at else None,
                        "details": {
                            k: v
                            for k, v in r.details.items()
                            if k not in {"previous_status", "status", "new_status", "id"}
                        }
                        or None,
                    }
                )

        return history
