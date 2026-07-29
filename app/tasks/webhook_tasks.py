import logging
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal
from app.services.audit_log import audit_log
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.webhook_tasks.dispatch_webhook_delivery",
    max_retries=5,
    default_retry_delay=30,
)
def dispatch_webhook_delivery(self, delivery_id: str) -> dict[str, Any]:
    """Deliver a single WebhookDelivery record asynchronously."""
    db = SessionLocal()
    try:
        from app.services.webhook_service import dispatch_delivery
        dispatch_delivery(db, UUID(delivery_id))
        logger.info("Webhook delivery %s dispatched.", delivery_id)
        return {"delivery_id": delivery_id, "dispatched": True}
    except Exception as exc:
        error_msg = str(exc)
        logger.exception("Failed to dispatch webhook delivery %s: %s", delivery_id, error_msg)
        
        # Log retry attempt if we have retries left
        if self.request.retries < self.max_retries:
            audit_log.log_event(
                db,
                event_type="webhook_retried",
                details={
                    "delivery_id": delivery_id,
                    "retry_count": self.request.retries + 1,
                    "error": error_msg
                }
            )
        
        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.webhook_tasks.retry_pending_webhook_deliveries",
)
def retry_pending_webhook_deliveries() -> dict[str, Any]:
    """
    Periodic beat task: finds all due RETRYING deliveries and re-dispatches them.
    Registered in celery_app.conf.beat_schedule to run every 60 seconds.
    """
    db = SessionLocal()
    try:
        from app.services.webhook_service import retry_pending_deliveries
        count = retry_pending_deliveries(db)
        logger.info("Retried %d pending webhook deliveries.", count)
        return {"retried": count}
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="app.tasks.webhook_tasks.trigger_sla_violation_async",
    max_retries=3,
    default_retry_delay=15,
)
def trigger_sla_violation_async(
    self, sla_data: dict[str, Any], event: str = "sla.violation"
) -> dict[str, Any]:
    """
    Async task wrapper around webhook_service.trigger_sla_violation_webhooks.
    Called from SLA computation tasks to avoid blocking.
    """
    db = SessionLocal()
    try:
        from app.models.webhook import WebhookEvent
        from app.services.webhook_service import trigger_sla_violation_webhooks

        deliveries = trigger_sla_violation_webhooks(
            db, sla_data=sla_data, event=WebhookEvent(event)
        )
        logger.info("Triggered %d webhook deliveries for event=%s.", len(deliveries), event)
        return {"triggered": len(deliveries), "event": event}
    except Exception as exc:
        error_msg = str(exc)
        logger.exception("trigger_sla_violation_async failed: %s", error_msg)
        
        # Log retry attempt if we have retries left
        if self.request.retries < self.max_retries:
            audit_log.log_event(
                db,
                event_type="sla_violation_webhook_retried",
                details={
                    "sla_data": sla_data,
                    "event": event,
                    "retry_count": self.request.retries + 1,
                    "error": error_msg
                }
            )
        
        raise self.retry(exc=exc)
    finally:
        db.close()
