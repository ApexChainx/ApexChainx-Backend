"""Periodic cleanup of expired auth rows (sessions and orphaned token families)."""

import logging

from app.db.session import SessionLocal
from app.repositories.session_repository import SessionRepository
from app.repositories.token_family_repository import TokenFamilyRepository
from app.services.audit_log import audit_log
from app.services.metrics import increment_counter
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.auth_tasks.cleanup_expired_auth_rows",
)
def cleanup_expired_auth_rows() -> dict:
    """
    Beat task: delete expired sessions in batches, then delete token families
    that no longer have any sessions. Registered in celery_app.conf.beat_schedule.
    """
    db = SessionLocal()
    try:
        deleted_sessions = SessionRepository(db).delete_expired_sessions()
        deleted_families = TokenFamilyRepository(db).delete_orphaned_families()

        increment_counter(
            "auth_rows_cleanup_deleted", value=deleted_sessions, tags={"row_type": "session"}
        )
        increment_counter(
            "auth_rows_cleanup_deleted", value=deleted_families, tags={"row_type": "token_family"}
        )

        if deleted_sessions or deleted_families:
            audit_log.log_event(
                db,
                event_type="auth_rows_cleanup",
                details={
                    "deleted_sessions": deleted_sessions,
                    "deleted_families": deleted_families,
                },
            )

        logger.info("Auth cleanup complete: %d sessions, %d families", deleted_sessions, deleted_families)
        return {"deleted_sessions": deleted_sessions, "deleted_families": deleted_families}
    finally:
        db.close()
