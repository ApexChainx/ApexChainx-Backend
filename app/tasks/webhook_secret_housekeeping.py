"""Scheduled task to expire old webhook secrets after the grace period."""

from datetime import UTC, datetime

from app.db.session import SessionLocal
from app.models.webhook import Webhook
from app.services.metrics import increment_counter
from app.tasks.celery_app import celery_app
from app.utils.secret_history import prune_expired_secrets


@celery_app.task(
    name="app.tasks.webhook_secret_housekeeping.expire_old_secrets",
)
def expire_old_secrets() -> dict:
    """Remove expired previous_secrets from all webhooks. Returns the number removed.

    #502: the rotate endpoint prunes the same way (via the shared
    ``app.utils.secret_history`` helper), so a webhook that is never rotated
    again still stops carrying dead entries, and this sweep is a backstop for
    webhooks that are not being touched.  The helper is deliberately tolerant of
    malformed rows, which previously raised out of the whole batch.
    """
    from sqlalchemy.orm import Session

    db: Session = SessionLocal()
    removed = 0
    try:
        webhooks = db.query(Webhook).all()
        now = datetime.now(UTC)
        for webhook in webhooks:
            if not webhook.previous_secrets:
                continue
            active, dropped = prune_expired_secrets(webhook.previous_secrets, now)
            if dropped:
                removed += dropped
                webhook.previous_secrets = active
        if removed:
            db.commit()
            increment_counter("webhook_secrets_expired", value=removed)
    finally:
        db.close()
    return {"removed": removed}


def run():
    expire_old_secrets()
