import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.lock import blocking_advisory_lock
from app.db.session import AuditSessionLocal, SessionLocal
from app.models.orm.audit_log import AuditLogORM
from app.services.formatters import canonical_json
from app.services.scrubber import scrub_details
from app.utils.correlation_ctx import get_correlation_id

# --- SLA Settlement Audit Event Types ---
SLA_SETTLEMENT_INITIATED = "sla_settlement_initiated"
SLA_SETTLEMENT_SUCCEEDED = "sla_settlement_succeeded"
SLA_SETTLEMENT_FAILED = "sla_settlement_failed"
SLA_DISPUTE_FILED = "sla_dispute_filed"
SLA_DISPUTE_RESOLVED = "sla_dispute_resolved"


class AuditLogService:
    def __init__(self, db_session_factory=None):
        self.db_session_factory = db_session_factory or SessionLocal
        self._last_hash: str | None = None

    @staticmethod
    def _compute_entry_hash(
        prev_hash: str | None,
        event_type: str,
        details: dict[str, Any] | None,
        correlation_id: str | None,
        created_at: datetime,
    ) -> str:
        data = {
            "prev_hash": prev_hash,
            "event_type": event_type,
            "details": details,
            "correlation_id": correlation_id,
            "created_at": created_at.isoformat() if created_at else None,
        }
        raw = canonical_json(data)
        return hashlib.sha256(raw.encode()).hexdigest()

    def log_event(
        self,
        db: Session,
        event_type: str,
        email: str | None = None,
        actor_id: str | None = None,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        safe_details = scrub_details(details)

        if correlation_id is None:
            correlation_id = get_correlation_id()

        created_at = datetime.now(UTC)
        # Serialize read-modify-write on the chain tail so concurrent writers
        # never fork the hash chain (single linear chain invariant).
        with blocking_advisory_lock(db, "audit_chain"):
            last_entry = db.query(AuditLogORM).order_by(desc(AuditLogORM.id)).first()
            prev_hash = last_entry.entry_hash if last_entry else None
            entry_hash = self._compute_entry_hash(
                prev_hash, event_type, safe_details, correlation_id, created_at
            )

            audit_entry = AuditLogORM(
                event_type=event_type,
                email=email,
                actor_id=actor_id,
                correlation_id=correlation_id,
                details=safe_details,
                created_at=created_at,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
            )
            db.add(audit_entry)
            db.commit()
        self._last_hash = entry_hash

    def list(self) -> list[dict[str, Any]]:
        factory = AuditSessionLocal if settings.DATABASE_AUDIT_URL else self.db_session_factory
        with factory() as db:
            entries = db.query(AuditLogORM).order_by(desc(AuditLogORM.id)).all()
            return [
                {
                    "id": entry.id,
                    "event_type": entry.event_type,
                    "email": entry.email,
                    "actor_id": entry.actor_id,
                    "correlation_id": entry.correlation_id,
                    "details": entry.details,
                    "created_at": entry.created_at,
                    "prev_hash": entry.prev_hash,
                    "entry_hash": entry.entry_hash,
                }
                for entry in entries
            ]

    def verify_chain(self) -> dict[str, Any]:
        factory = AuditSessionLocal if settings.DATABASE_AUDIT_URL else self.db_session_factory
        with factory() as db:
            entries = db.query(AuditLogORM).order_by(AuditLogORM.id.asc()).all()
            total = len(entries)
            if total == 0:
                return {"verified": True, "first_bad_id": None, "total_entries": 0, "last_hash": None}

            for index, entry in enumerate(entries):
                previous_hash = entries[index - 1].entry_hash if index else None
                expected_hash = self._compute_entry_hash(
                    previous_hash,
                    entry.event_type,
                    entry.details,
                    entry.correlation_id,
                    entry.created_at,
                )
                if entry.prev_hash != previous_hash or entry.entry_hash != expected_hash:
                    return {
                        "verified": False,
                        "first_bad_id": entry.id,
                        "total_entries": total,
                        "last_hash": entries[-1].entry_hash,
                    }

            return {
                "verified": True,
                "first_bad_id": None,
                "total_entries": total,
                "last_hash": entries[-1].entry_hash,
            }

    def log(self, event_type: str, details: dict[str, Any] | None = None) -> None:
        """
        Simplified log method for compatibility with existing code.
        Uses its own session if not provided.

        When DATABASE_AUDIT_URL is configured, writes go through the
        audit-specific DB role/connection for least-privilege isolation.
        """
        factory = AuditSessionLocal if settings.DATABASE_AUDIT_URL else self.db_session_factory
        with factory() as db:
            self.log_event(db, event_type, details=details)


audit_log = AuditLogService()
