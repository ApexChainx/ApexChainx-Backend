import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from celery import Task

from app.tasks.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.job import Job, JobStatus, JobType
from app.models.webhook import WebhookEvent
from app.services.audit_log import audit_log
from app.utils.correlation_ctx import set_correlation_id
from app.utils.logging import get_structured_logger

logger = logging.getLogger(__name__)
task_logger = get_structured_logger("sla_tasks")


class DatabaseTask(Task):
    """Base task that provides a scoped DB session and updates Job records."""

    abstract = True
    _db = None

    def get_db(self):
        return SessionLocal()

    def _get_job(self, db, celery_task_id: str) -> Optional[Job]:
        return db.query(Job).filter(Job.celery_task_id == celery_task_id).first()

    def _mark_started(self, db, celery_task_id: str):
        job = self._get_job(db, celery_task_id)
        if job:
            job.status = JobStatus.STARTED
            job.started_at = datetime.utcnow()
            db.commit()

    def _mark_success(self, db, celery_task_id: str, result: Any):
        job = self._get_job(db, celery_task_id)
        if job:
            job.status = JobStatus.SUCCESS
            job.result = json.dumps(result)
            job.progress = 100.0
            job.finished_at = datetime.utcnow()
            db.commit()

    def _mark_failure(self, db, celery_task_id: str, error: str):
        job = self._get_job(db, celery_task_id)
        if job:
            job.status = JobStatus.FAILURE
            job.error = error
            job.finished_at = datetime.utcnow()
            db.commit()

    def _update_progress(self, db, celery_task_id: str, progress: float, details: Optional[Dict[str, Any]] = None):
        job = self._get_job(db, celery_task_id)
        if job:
            job.progress = min(progress, 99.0)
            if details:
                job.progress_details = details
            db.commit()

    def _add_partial_result(self, db, celery_task_id: str, item_id: str, result: Any):
        """Add a partial result for bulk operations."""
        job = self._get_job(db, celery_task_id)
        if job:
            if not job.partial_results:
                job.partial_results = {}
            job.partial_results[item_id] = result
            db.commit()

    def _add_item_error(self, db, celery_task_id: str, item_id: str, error: str):
        """Add an error for a specific item in bulk operations."""
        job = self._get_job(db, celery_task_id)
        if job:
            if not job.per_item_errors:
                job.per_item_errors = {}
            job.per_item_errors[item_id] = error
            db.commit()

    def _log_retry(self, db, celery_task_id: str, retry_count: int, error: str):
        """Log job retry events for audit purposes."""
        job = self._get_job(db, celery_task_id)
        if job:
            audit_log.log_event(
                db,
                event_type="job_retried",
                details={
                    "job_id": str(job.id),
                    "celery_task_id": celery_task_id,
                    "job_type": job.job_type.value,
                    "retry_count": retry_count,
                    "error": error,
                    "payload": job.payload,
                },
            )


@celery_app.task(
    bind=True,
    base=DatabaseTask,
    name="app.tasks.sla_tasks.compute_sla_for_device",
    max_retries=3,
    default_retry_delay=30,
)
def compute_sla_for_device(
    self: DatabaseTask, device_id: str, period: str, correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Compute SLA metrics for a single device over a given period.
    Triggers SLA violation webhooks if thresholds are breached.
    """
    # Set correlation ID for this task execution
    if correlation_id:
        set_correlation_id(correlation_id)

    db = self.get_db()
    try:
        self._mark_started(db, self.request.id)
        task_logger.info(
            "Starting SLA computation",
            device_id=device_id,
            period=period,
            celery_task_id=self.request.id,
            correlation_id=correlation_id,
        )

        # ------------------------------------------------------------------ #
        # SLA computation logic — replace with actual domain implementation   #
        # ------------------------------------------------------------------ #
        from app.services.sla_service import compute_device_sla  # type: ignore

        # Update progress with structured details
        self._update_progress(
            db, self.request.id, 30.0, {"stage": "data_collection", "device_id": device_id, "period": period}
        )

        result = compute_device_sla(db, device_id=device_id, period=period)

        self._update_progress(
            db,
            self.request.id,
            70.0,
            {
                "stage": "sla_computation_complete",
                "device_id": device_id,
                "period": period,
                "is_violated": result.get("is_violated", False),
            },
        )

        # Check for violations and dispatch webhooks
        if result.get("is_violated"):
            self._update_progress(
                db,
                self.request.id,
                85.0,
                {"stage": "triggering_webhooks", "device_id": device_id, "period": period, "violation_detected": True},
            )

            from app.services.webhook_service import trigger_sla_violation_webhooks

            trigger_sla_violation_webhooks(
                db,
                sla_data={
                    "device_id": device_id,
                    "period": period,
                    **result,
                },
                event=WebhookEvent.SLA_VIOLATION,
            )

        self._update_progress(
            db, self.request.id, 95.0, {"stage": "finalizing", "device_id": device_id, "period": period}
        )

        self._mark_success(db, self.request.id, result)
        logger.info("SLA computation complete for device=%s", device_id)
        return result

    except Exception as exc:
        error_msg = str(exc)
        logger.exception("SLA computation failed for device=%s: %s", device_id, error_msg)

        # Log retry attempt if we have retries left
        if self.request.retries < self.max_retries:
            self._log_retry(db, self.request.id, self.request.retries + 1, error_msg)

        self._mark_failure(db, self.request.id, error_msg)
        raise self.retry(exc=exc)
    finally:
        db.close()


@celery_app.task(
    bind=True,
    base=DatabaseTask,
    name="app.tasks.sla_tasks.compute_bulk_sla",
    max_retries=2,
    default_retry_delay=60,
)
def compute_bulk_sla(self: DatabaseTask, device_ids: List[str], period: str) -> Dict[str, Any]:
    """
    Compute SLA for multiple devices. Dispatches individual tasks per device
    and tracks overall progress.
    """
    db = self.get_db()
    try:
        self._mark_started(db, self.request.id)
        total = len(device_ids)
        logger.info("Starting bulk SLA computation for %d devices, period=%s", total, period)

        # Initialize progress tracking
        self._update_progress(
            db, self.request.id, 5.0, {"stage": "initialization", "total_devices": total, "period": period}
        )

        results = []
        violations = []
        processed_count = 0
        error_count = 0

        for idx, device_id in enumerate(device_ids, start=1):
            try:
                from app.services.sla_service import compute_device_sla  # type: ignore

                result = compute_device_sla(db, device_id=device_id, period=period)
                results.append({"device_id": device_id, "result": result})

                # Store partial result
                self._add_partial_result(db, self.request.id, device_id, result)

                if result.get("is_violated"):
                    violations.append(device_id)
                    from app.services.webhook_service import trigger_sla_violation_webhooks

                    trigger_sla_violation_webhooks(
                        db,
                        sla_data={"device_id": device_id, "period": period, **result},
                        event=WebhookEvent.SLA_VIOLATION,
                    )

                processed_count += 1

            except Exception as device_exc:
                logger.warning("SLA failed for device=%s: %s", device_id, device_exc)
                results.append({"device_id": device_id, "error": str(device_exc)})

                # Store per-item error
                self._add_item_error(db, self.request.id, device_id, str(device_exc))
                error_count += 1

            # Update progress with detailed information
            progress = (idx / total) * 100
            self._update_progress(
                db,
                self.request.id,
                progress,
                {
                    "stage": "processing_devices",
                    "current_device": device_id,
                    "processed_count": processed_count,
                    "error_count": error_count,
                    "total_devices": total,
                    "violations_found": len(violations),
                    "progress_percentage": round(progress, 2),
                },
            )

        # Final summary with structured progress
        self._update_progress(
            db,
            self.request.id,
            95.0,
            {
                "stage": "finalizing",
                "total_devices": total,
                "processed_count": processed_count,
                "error_count": error_count,
                "violations_found": len(violations),
            },
        )

        summary = {
            "total": total,
            "violations": len(violations),
            "violated_devices": violations,
            "processed_count": processed_count,
            "error_count": error_count,
            "results": results,
        }

        self._mark_success(db, self.request.id, summary)
        logger.info("Bulk SLA computation complete. Violations: %d/%d, Errors: %d", len(violations), total, error_count)
        return summary

    except Exception as exc:
        error_msg = str(exc)
        logger.exception("Bulk SLA computation failed: %s", error_msg)

        # Log retry attempt if we have retries left
        if self.request.retries < self.max_retries:
            self._log_retry(db, self.request.id, self.request.retries + 1, error_msg)

        self._mark_failure(db, self.request.id, error_msg)
        raise self.retry(exc=exc)
    finally:
        db.close()


def enqueue_sla_computation(
    db,
    device_id: str,
    period: str,
    job_type: JobType = JobType.SLA_COMPUTATION,
    correlation_id: Optional[str] = None,
) -> Job:
    """
    Enqueue an SLA computation task and create a Job record for tracking.
    Returns the Job before the Celery task ID is known — updated after dispatch.
    """
    from app.models.job import Job  # local import avoids circular deps

    payload = {"device_id": device_id, "period": period}
    if correlation_id:
        payload["correlation_id"] = correlation_id

    task_result = compute_sla_for_device.apply_async(kwargs=payload)

    job = Job(
        celery_task_id=task_result.id,
        job_type=job_type,
        payload=json.dumps(payload),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def enqueue_bulk_sla_computation(db, device_ids: List[str], period: str, correlation_id: Optional[str] = None) -> Job:
    """Enqueue a bulk SLA computation task and return the tracking Job."""
    from app.models.job import Job, JobType

    payload = {"device_ids": device_ids, "period": period}
    if correlation_id:
        payload["correlation_id"] = correlation_id

    task_result = compute_bulk_sla.apply_async(kwargs=payload)

    job = Job(
        celery_task_id=task_result.id,
        job_type=JobType.BULK_SLA_COMPUTATION,
        payload=json.dumps(payload),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
