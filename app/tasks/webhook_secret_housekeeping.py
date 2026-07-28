"""Scheduled task to expire old webhook secrets after the grace period."""
from datetime import datetime, timezone
from app.db.session import SessionLocal
from app.models.webhook import Webhook


def expire_old_secrets():
    """Remove expired previous_secrets from all webhooks."""
    from sqlalchemy.orm import Session
    db: Session = SessionLocal()
    try:
        webhooks = db.query(Webhook).all()
        now = datetime.now(timezone.utc)
        for webhook in webhooks:
            if not webhook.previous_secrets:
                continue
            active = [
                s for s in webhook.previous_secrets
                if datetime.fromisoformat(s["expires_at"]) > now
            ]
            if len(active) != len(webhook.previous_secrets):
                webhook.previous_secrets = active
        db.commit()
    finally:
        db.close()


def run():
    expire_old_secrets()