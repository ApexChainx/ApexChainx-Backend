from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "apexchainx",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.auth_tasks",
        "app.tasks.outage_tasks",
        "app.tasks.audit_tasks",
        "app.tasks.sla_tasks",
        "app.tasks.webhook_secret_housekeeping",
        "app.tasks.webhook_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_store_eager_result=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,  # 24 hours
    beat_schedule={
        "retry-pending-webhook-deliveries": {
            "task": "app.tasks.webhook_tasks.retry_pending_webhook_deliveries",
            "schedule": 60.0,  # every 60 seconds
        },
        "cleanup-expired-auth-rows": {
            "task": "app.tasks.auth_tasks.cleanup_expired_auth_rows",
            "schedule": 3600.0,  # every hour
        },
        "expire-old-webhook-secrets": {
            "task": "app.tasks.webhook_secret_housekeeping.expire_old_secrets",
            "schedule": 86400.0,  # every day
        },
        "cleanup-old-outage-events": {
            "task": "app.tasks.outage_tasks.cleanup_old_outage_events",
            "schedule": 86400.0,  # every day
        },
        "archive-old-audit-entries": {
            "task": "app.tasks.audit_tasks.archive_old_audit_entries",
            "schedule": 86400.0,  # every day
        },
    },
)
