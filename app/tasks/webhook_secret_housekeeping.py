"""Scheduled task to expire old webhook secrets after the grace period."""

from datetime import UTC, datetime

from app.db.session import SessionLocal
from app.models.webhook import Webhook
from app.services.metrics import increment_counter
from app.tasks.celery_app import celery_app


@celery_app.task(
    name="app.tasks.webhook_secret_housekeeping.expire_old_secrets",
)
def expire_old_secrets() -> dict:
    """Remove expired previous_secrets from all webhooks. Returns the number removed."""
    from sqlalchemy.orm import Session

    db: Session = SessionLocal()
    removed = 0
    try:
        webhooks = db.query(Webhook).all()
        now = datetime.now(UTC)
        for webhook in webhooks:
            if not webhook.previous_secrets:
                continue
            active = [s for s in webhook.previous_secrets if datetime.fromisoformat(s["expires_at"]) > now]
            if len(active) != len(webhook.previous_secrets):
                removed += len(webhook.previous_secrets) - len(active)
                webhook.previous_secrets = active
        if removed:
            db.commit()
            increment_counter("webhook_secrets_expired", value=removed)
    finally:
        db.close()
    return {"removed": removed}


def run():
    expire_old_secrets()
