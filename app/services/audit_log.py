from datetime import datetime, timezone
from typing import Any, Optional
import hashlib
import json
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.models.orm.audit_log import AuditLogORM
from app.db.session import SessionLocal
from app.utils.correlation import get_correlation_id
from app.services.scrubber import scrub_details


class AuditLogService:
    def __init__(self, db_session_factory=None):
        self.db_session_factory = db_session_factory or SessionLocal
        self._last_hash: Optional[str] = None

    @staticmethod
    def _compute_entry_hash(
        prev_hash: Optional[str],
        event_type: str,
        details: Optional[dict[str, Any]],
        correlation_id: Optional[str],
        created_at: datetime,
    ) -> str:
        data = {
            "prev_hash": prev_hash,
            "event_type": event_type,
            "details": details,
            "correlation_id": correlation_id,
            "created_at": created_at.isoformat() if created_at else None,
        }
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def log_event(
        self,
        db: Session,
        event_type: str,
        email: Optional[str] = None,
        actor_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None
    ) -> None:
        safe_details = scrub_details(details)

        if correlation_id is None:
            correlation_id = get_correlation_id()

        created_at = datetime.now(timezone.utc)
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

    def log(self, event_type: str, details: Optional[dict[str, Any]] = None) -> None:
        with self.db_session_factory() as db:
            self.log_event(db, event_type, details=details)


audit_log = AuditLogService()
