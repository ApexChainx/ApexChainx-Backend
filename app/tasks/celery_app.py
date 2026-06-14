from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "apexchainx",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.sla_tasks",
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
    },
)
