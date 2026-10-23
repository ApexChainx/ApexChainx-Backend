"""Periodic retention cleanup for the outage event timeline."""

import logging
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.db.session import SessionLocal
from app.repositories.outage_event_repository import OutageEventRepository
from app.services.metrics import increment_counter
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.outage_tasks.cleanup_old_outage_events",
)
def cleanup_old_outage_events() -> dict:
    """Delete outage timeline events older than the retention window in batches."""
    db = SessionLocal()
    try:
        cutoff = datetime.now(UTC) - timedelta(days=settings.OUTAGE_EVENT_RETENTION_DAYS)
        deleted = OutageEventRepository(db).delete_events_older_than(cutoff)
        increment_counter("outage_events_cleanup_deleted", value=deleted)
        logger.info(
            "Outage event cleanup removed %d events older than %s",
            deleted,
            cutoff.isoformat(),
        )
        return {"deleted": deleted, "cutoff": cutoff.isoformat()}
    finally:
        db.close()
