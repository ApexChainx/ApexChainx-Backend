"""Periodic retention cleanup for the audit log (archive then delete)."""

import json
import logging
import os
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.orm.audit_log import AuditLogORM
from app.services.metrics import increment_counter
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000


@celery_app.task(
    name="app.tasks.audit_tasks.archive_old_audit_entries",
)
def archive_old_audit_entries() -> dict:
    """Archive audit entries older than AUDIT_RETENTION_DAYS to JSONL, then delete them.

    Archived records preserve the hash chain (prev_hash/entry_hash) and
    correlation IDs. Current-period rows are never touched.
    """
    db = SessionLocal()
    cutoff = datetime.now(UTC) - timedelta(days=settings.AUDIT_RETENTION_DAYS)
    archived = 0
    try:
        os.makedirs(settings.AUDIT_ARCHIVE_DIR, exist_ok=True)
        archive_path = os.path.join(
            settings.AUDIT_ARCHIVE_DIR,
            f"audit_archive_{datetime.now(UTC).date().isoformat()}.jsonl",
        )

        while True:
            rows = (
                db.query(AuditLogORM)
                .filter(AuditLogORM.created_at < cutoff)
                .order_by(AuditLogORM.id.asc())
                .limit(_BATCH_SIZE)
                .all()
            )
            if not rows:
                break

            ids = []
            with open(archive_path, "a", encoding="utf-8") as fh:
                for row in rows:
                    ids.append(row.id)
                    record = {
                        "id": row.id,
                        "event_type": row.event_type,
                        "email": row.email,
                        "actor_id": row.actor_id,
                        "correlation_id": row.correlation_id,
                        "details": row.details,
                        "created_at": row.created_at.isoformat(),
                        "prev_hash": row.prev_hash,
                        "entry_hash": row.entry_hash,
                    }
                    fh.write(json.dumps(record, default=str) + "\n")

            db.query(AuditLogORM).filter(AuditLogORM.id.in_(ids)).delete(
                synchronize_session=False
            )
            db.commit()
            archived += len(ids)

        increment_counter("audit_entries_archived", value=archived)
        logger.info("Archived %d audit entries older than %s", archived, cutoff.isoformat())
        return {"archived": archived, "cutoff": cutoff.isoformat()}
    finally:
        db.close()
