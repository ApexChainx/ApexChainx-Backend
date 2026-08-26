import builtins
from datetime import UTC, datetime

from sqlalchemy import and_, asc, desc, func, or_
from sqlalchemy.orm import Session

from app.models.enums import OutageStatus, Severity
from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM
from app.models.outage import Location, Outage, SLAStatus
from app.models.outage_dto import OutageCreate, OutageSortDirection, OutageSortField, OutageUpdate
from app.utils.cursor import CursorPage, decode_cursor, encode_cursor


def _orm_to_pydantic(orm: OutageORM) -> Outage:
    location = None
    if orm.location:
        location = Location(**orm.location)

    sla_status = None
    if orm.sla_status:
        sla_status = SLAStatus(**orm.sla_status)

    return Outage(
        id=orm.id,
        site_name=orm.site_name,
        site_id=orm.site_id,
        severity=orm.severity,
        status=orm.status,
        detected_at=orm.detected_at,
        resolved_at=orm.resolved_at,
        description=orm.description,
        affected_services=orm.affected_services or [],
        affected_subscribers=orm.affected_subscribers,
        assigned_to=orm.assigned_to,
        created_by=orm.created_by,
        location=location,
        sla_status=sla_status,
    )


ALLOWED_STATUS_TRANSITIONS = {
    OutageStatus.open.value: {OutageStatus.open.value, OutageStatus.resolved.value},
    OutageStatus.resolved.value: {OutageStatus.resolved.value},
}

OUTAGE_SORT_FIELDS = {"detected_at", "site_name", "severity", "status", "id"}


class OutageRepository:
    def __init__(self, db: Session):
        self.db = db

    def list(
        self,
        severity: Severity | None = None,
        status: OutageStatus | None = None,
        search: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: OutageSortField = OutageSortField.detected_at,
        sort_direction: OutageSortDirection = OutageSortDirection.desc,
        include_total: bool = True,
    ) -> dict:
        query = self.db.query(OutageORM)

        if severity:
            query = query.filter(OutageORM.severity == severity.value)
        if status:
            query = query.filter(OutageORM.status == status.value)

        if search:
            search_filter = or_(
                OutageORM.id.ilike(f"%{search}%"),
                OutageORM.site_id.ilike(f"%{search}%"),
                OutageORM.site_name.ilike(f"%{search}%"),
            )
            query = query.filter(search_filter)

        if start_date:
            query = query.filter(OutageORM.detected_at >= start_date)
        if end_date:
            query = query.filter(OutageORM.detected_at <= end_date)

        sort_column = getattr(OutageORM, sort_by.value)
        direction_fn = asc if sort_direction == OutageSortDirection.asc else desc
        query = query.order_by(direction_fn(sort_column), OutageORM.id.asc())

        if include_total:
            # Use COUNT(*) OVER() window function to get total count in the same
            # query pass as the page items — avoids a second round-trip to the DB.
            rows = query.add_columns(func.count().over()).offset((page - 1) * page_size).limit(page_size).all()
            total = rows[0][1] if rows else 0
            items = [row[0] for row in rows]
        else:
            total = None
            items = query.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "items": [_orm_to_pydantic(o) for o in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by.value,
            "sort_direction": sort_direction.value,
        }

    def list_cursor(
        self,
        severity: Severity | None = None,
        status: OutageStatus | None = None,
        search: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        cursor: str | None = None,
        limit: int = 20,
        sort_by: OutageSortField = OutageSortField.detected_at,
        sort_direction: OutageSortDirection = OutageSortDirection.desc,
    ) -> CursorPage:
        """Cursor-based pagination for outages.

        Returns a CursorPage with items, next_cursor, and has_more.
        O(1) per page — stable under concurrent writes.
        """
        query = self.db.query(OutageORM)

        if severity:
            query = query.filter(OutageORM.severity == severity.value)
        if status:
            query = query.filter(OutageORM.status == status.value)

        if search:
            search_filter = or_(
                OutageORM.id.ilike(f"%{search}%"),
                OutageORM.site_id.ilike(f"%{search}%"),
                OutageORM.site_name.ilike(f"%{search}%"),
            )
            query = query.filter(search_filter)

        if start_date:
            query = query.filter(OutageORM.detected_at >= start_date)
        if end_date:
            query = query.filter(OutageORM.detected_at <= end_date)

        sort_column = getattr(OutageORM, sort_by.value)
        direction_fn = asc if sort_direction == OutageSortDirection.asc else desc

        # Apply cursor filter if provided
        decoded = decode_cursor(cursor)
        if decoded is not None:
            cursor_id, cursor_value = decoded
            # Use the sort column and id for stable cursor-based filtering
            sort_attr = getattr(OutageORM, sort_by.value)
            if sort_direction == OutageSortDirection.desc:
                cursor_filter = or_(
                    sort_attr < cursor_value,
                    and_(sort_attr == cursor_value, OutageORM.id < cursor_id),
                )
            else:
                cursor_filter = or_(
                    sort_attr > cursor_value,
                    and_(sort_attr == cursor_value, OutageORM.id > cursor_id),
                )
            query = query.filter(cursor_filter)

        query = query.order_by(direction_fn(sort_column), OutageORM.id.asc())

        # Fetch limit+1 to determine has_more
        items = query.limit(limit + 1).all()
        has_more = len(items) > limit
        items = items[:limit]

        next_cursor = None
        if has_more and items:
            last = items[-1]
            sort_value = str(getattr(last, sort_by.value))
            next_cursor = encode_cursor(last.id, sort_value)

        return CursorPage(
            items=[_orm_to_pydantic(o) for o in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def list_all(self) -> builtins.list[Outage]:
        rows = self.db.query(OutageORM).all()
        return [_orm_to_pydantic(r) for r in rows]

    def list_filtered(
        self,
        severity: Severity | None = None,
        status: OutageStatus | None = None,
        search: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> builtins.list[Outage]:
        query = self.db.query(OutageORM)
        if severity:
            query = query.filter(OutageORM.severity == severity.value)
        if status:
            query = query.filter(OutageORM.status == status.value)
        if search:
            query = query.filter(
                or_(
                    OutageORM.id.ilike(f"%{search}%"),
                    OutageORM.site_id.ilike(f"%{search}%"),
                    OutageORM.site_name.ilike(f"%{search}%"),
                )
            )
        if start_date:
            query = query.filter(OutageORM.detected_at >= start_date)
        if end_date:
            query = query.filter(OutageORM.detected_at <= end_date)
        return [_orm_to_pydantic(r) for r in query.all()]

    def iter_filtered(
        self,
        severity: Severity | None = None,
        status: OutageStatus | None = None,
        search: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        batch_size: int = 200,
    ):
        """Stream filtered outages in batches to avoid loading all rows into memory."""
        query = self.db.query(OutageORM)
        if severity:
            query = query.filter(OutageORM.severity == severity.value)
        if status:
            query = query.filter(OutageORM.status == status.value)
        if search:
            query = query.filter(
                or_(
                    OutageORM.id.ilike(f"%{search}%"),
                    OutageORM.site_id.ilike(f"%{search}%"),
                    OutageORM.site_name.ilike(f"%{search}%"),
                )
            )
        if start_date:
            query = query.filter(OutageORM.detected_at >= start_date)
        if end_date:
            query = query.filter(OutageORM.detected_at <= end_date)
        yield from (_orm_to_pydantic(r) for r in query.yield_per(batch_size))

    def get(self, outage_id: str) -> Outage | None:
        row = self.db.query(OutageORM).filter(OutageORM.id == outage_id).first()
        if not row:
            return None
        return _orm_to_pydantic(row)

    def get_orm(self, outage_id: str) -> OutageORM | None:
        return self.db.query(OutageORM).filter(OutageORM.id == outage_id).first()

    def get_orm_locked(self, outage_id: str) -> OutageORM | None:
        """Acquire a row-level lock (SELECT FOR UPDATE) before mutating."""
        return self.db.query(OutageORM).filter(OutageORM.id == outage_id).with_for_update().first()

    @staticmethod
    def validate_status_transition(current_status: str, next_status: str) -> None:
        allowed = ALLOWED_STATUS_TRANSITIONS.get(current_status, set())
        if next_status not in allowed:
            raise ValueError(f"Invalid status transition: {current_status} -> {next_status}")

    def _find_duplicate_orm(self, payload: OutageCreate) -> OutageORM | None:
        query = self.db.query(OutageORM).filter(
            and_(
                OutageORM.site_name == payload.site_name,
                OutageORM.detected_at == payload.detected_at,
                OutageORM.description == payload.description,
            )
        )
        if payload.site_id:
            query = query.filter(OutageORM.site_id == payload.site_id)
        return query.first()

    @staticmethod
    def _is_same_outage(orm: OutageORM, payload: OutageCreate) -> bool:
        return (
            orm.id == payload.id
            and orm.site_name == payload.site_name
            and orm.site_id == payload.site_id
            and orm.severity == payload.severity.value
            and orm.status == payload.status.value
            and orm.detected_at == payload.detected_at
            and orm.description == payload.description
            and (orm.affected_services or []) == payload.affected_services
            and orm.affected_subscribers == payload.affected_subscribers
            and orm.assigned_to == payload.assigned_to
            and orm.created_by == payload.created_by
            and (orm.location or None) == (payload.location.model_dump() if payload.location else None)
        )

    def check_duplicate(self, payload: OutageCreate) -> Outage | None:
        existing_by_id = self.get_orm(payload.id)
        if existing_by_id:
            if self._is_same_outage(existing_by_id, payload):
                return _orm_to_pydantic(existing_by_id)
            raise ValueError(f"Outage with id '{payload.id}' already exists with different content")

        duplicate = self._find_duplicate_orm(payload)
        if duplicate:
            return _orm_to_pydantic(duplicate)
        return None

    def create_or_get_existing(self, payload: OutageCreate) -> tuple[Outage, bool]:
        """Create a new outage or return an existing duplicate.

        Returns a tuple of (outage, persisted).
        Persisted is False when the payload matches an existing outage.
        """
        existing = self.check_duplicate(payload)
        if existing:
            return existing, False

        location_data = payload.location.model_dump() if payload.location else None
        orm = OutageORM(
            id=payload.id,
            site_name=payload.site_name,
            site_id=payload.site_id,
            severity=payload.severity.value,
            status=payload.status.value,
            detected_at=payload.detected_at,
            description=payload.description,
            affected_services=payload.affected_services,
            affected_subscribers=payload.affected_subscribers,
            assigned_to=payload.assigned_to,
            created_by=payload.created_by,
            location=location_data,
        )
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm), True

    def create(self, payload: OutageCreate) -> Outage:
        outage, _ = self.create_or_get_existing(payload)
        return outage

    def bulk_create(self, outages: builtins.list[OutageCreate]) -> builtins.list[Outage]:
        return [self.create(payload) for payload in outages]

    def update(self, outage_id: str, payload: OutageUpdate) -> Outage | None:
        orm = self.get_orm(outage_id)
        if not orm:
            return None

        update_data = payload.model_dump(exclude_unset=True)
        if "status" in update_data and update_data["status"] is not None:
            next_status = update_data["status"]
            next_status_value = next_status.value if hasattr(next_status, "value") else str(next_status)
            self.validate_status_transition(orm.status, next_status_value)

        for key, value in update_data.items():
            if key == "location" and value is not None:
                setattr(orm, key, value if isinstance(value, dict) else value.model_dump())
            elif hasattr(value, "value"):  # enum
                setattr(orm, key, value.value)
            else:
                setattr(orm, key, value)

        orm.updated_at = datetime.now(UTC)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def has_financial_history(self, outage_id: str) -> bool:
        """Return True if the outage has SLA results or payments referencing it."""
        from app.models.orm.sla import SLAResultORM
        from app.models.orm.payment import PaymentTransactionORM

        sla_count = self.db.query(SLAResultORM).filter(SLAResultORM.outage_id == outage_id).count()
        if sla_count > 0:
            return True
        try:
            payment_count = self.db.query(PaymentTransactionORM).filter(PaymentTransactionORM.outage_id == outage_id).count()
            return payment_count > 0
        except Exception:
            # PaymentTransactionORM may not have outage_id in all schema versions
            return False

    def delete(self, outage_id: str) -> None:
        orm = self.get_orm(outage_id)
        if orm:
            if self.has_financial_history(outage_id):
                raise ValueError(
                    f"Cannot delete outage '{outage_id}': it has associated SLA results or payment records. "
                    "Archive the outage instead or contact an administrator."
                )
            self.db.delete(orm)
            self.db.commit()

    def resolve(self, outage_id: str, mttr_minutes: int) -> Outage | None:
        orm = self.get_orm_locked(outage_id)
        if not orm:
            return None

        # Idempotent: already resolved with same mttr → return as-is
        if orm.status == OutageStatus.resolved.value and orm.mttr_minutes == mttr_minutes:
            return _orm_to_pydantic(orm)

        self.validate_status_transition(orm.status, OutageStatus.resolved.value)
        orm.status = OutageStatus.resolved.value
        orm.mttr_minutes = mttr_minutes
        orm.resolved_at = datetime.now(UTC)
        orm.updated_at = datetime.now(UTC)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def list_violations(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        query = (
            self.db.query(SLAResultORM, OutageORM)
            .join(OutageORM, OutageORM.id == SLAResultORM.outage_id)
            .filter(SLAResultORM.is_latest.is_(True), SLAResultORM.status == "violated")
            .order_by(SLAResultORM.created_at.desc())
        )

        total = query.count()
        rows = query.offset((page - 1) * page_size).limit(page_size).all()

        items = []
        for sla_orm, outage_orm in rows:
            items.append(
                {
                    "outage": _orm_to_pydantic(outage_orm),
                    "sla": {
                        "id": sla_orm.id,
                        "outage_id": sla_orm.outage_id,
                        "status": sla_orm.status,
                        "mttr_minutes": sla_orm.mttr_minutes,
                        "threshold_minutes": sla_orm.threshold_minutes,
                        "amount": sla_orm.amount,
                        "payment_type": sla_orm.payment_type,
                        "rating": sla_orm.rating,
                        "policy_version": sla_orm.policy_version,
                        "threshold_source": sla_orm.threshold_source,
                        "reason_code": sla_orm.reason_code,
                        "decision_trace": sla_orm.decision_trace,
                        "compute_hash": sla_orm.compute_hash,
                    },
                }
            )

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
