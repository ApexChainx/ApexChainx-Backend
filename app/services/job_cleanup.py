"""Job retention and cleanup service.

BE-042: Provides job retention and cleanup policies to prevent unbounded growth
of job records and maintain database performance.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.db_retry import run_with_db_retry
from app.models.job import Job, JobStatus
from app.models.webhook import WebhookDelivery, WebhookDeliveryStatus

# BE-298: Terminal (non-in-flight) delivery statuses eligible for retention cleanup.
WEBHOOK_DELIVERY_TERMINAL_STATUSES = (WebhookDeliveryStatus.SUCCESS, WebhookDeliveryStatus.FAILED)
WEBHOOK_DELIVERY_RETENTION_DAYS = 30
WEBHOOK_DEAD_LETTER_RETENTION_DAYS = 90


def cleanup_old_webhook_deliveries(
    db: Session,
    retention_days: int = WEBHOOK_DELIVERY_RETENTION_DAYS,
    dead_letter_retention_days: int = WEBHOOK_DEAD_LETTER_RETENTION_DAYS,
    batch_size: int = 1000,
) -> int:
    """Batch-delete terminal webhook_deliveries rows older than the retention window.

    Mirrors JobCleanupService's batched-delete pattern. PENDING, RETRYING, and
    BREAKER_OPEN (in-flight) deliveries are never touched; DEAD_LETTER rows get
    their own (longer) retention period.
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    dead_letter_cutoff = datetime.now(UTC) - timedelta(days=dead_letter_retention_days)
    total_deleted = 0

    for statuses, cutoff_date in (
        (WEBHOOK_DELIVERY_TERMINAL_STATUSES, cutoff),
        ((WebhookDeliveryStatus.DEAD_LETTER,), dead_letter_cutoff),
    ):
        while True:
            ids = (
                db.query(WebhookDelivery.id)
                .filter(WebhookDelivery.status.in_(statuses), WebhookDelivery.created_at < cutoff_date)
                .limit(batch_size)
                .all()
            )
            ids = [row[0] for row in ids]
            if not ids:
                break
            db.execute(delete(WebhookDelivery).where(WebhookDelivery.id.in_(ids)))
            db.commit()
            total_deleted += len(ids)
            if len(ids) < batch_size:
                break

    return total_deleted


class JobCleanupService:
    """Manages job retention and cleanup policies."""

    # Default retention periods
    SUCCESSFUL_JOB_RETENTION_DAYS = 30  # Keep successful jobs for 30 days
    FAILED_JOB_RETENTION_DAYS = 90  # Keep failed jobs for 90 days (for debugging)

    def __init__(self, db: Session):
        self.db = db

    def cleanup_old_jobs(
        self,
        successful_retention_days: int | None = None,
        failed_retention_days: int | None = None,
        dry_run: bool = False,
        batch_size: int = 1000,
    ) -> dict:
        """Clean up old completed and failed jobs based on retention policy.

        Args:
            successful_retention_days: Days to keep successful jobs (default: 30)
            failed_retention_days: Days to keep failed jobs (default: 90)
            dry_run: If True, only count what would be deleted without deleting
            batch_size: Process deletions in batches to avoid long-running transactions

        Returns:
            Dictionary with cleanup statistics
        """
        successful_retention_days = successful_retention_days or self.SUCCESSFUL_JOB_RETENTION_DAYS
        failed_retention_days = failed_retention_days or self.FAILED_JOB_RETENTION_DAYS

        cutoff_success = datetime.now(UTC) - timedelta(days=successful_retention_days)
        cutoff_failed = datetime.now(UTC) - timedelta(days=failed_retention_days)

        total_deleted = 0
        stats = {
            "successful_jobs_deleted": 0,
            "failed_jobs_deleted": 0,
            "revoked_jobs_deleted": 0,
            "cutoff_successful": cutoff_success.isoformat(),
            "cutoff_failed": cutoff_failed.isoformat(),
            "dry_run": dry_run,
        }

        # Clean up old successful jobs
        success_count = self._count_jobs_by_status(JobStatus.SUCCESS, cutoff_success)
        stats["successful_jobs_deleted"] = success_count

        if not dry_run and success_count > 0:
            deleted = self._delete_jobs_by_status(JobStatus.SUCCESS, cutoff_success, batch_size)
            total_deleted += deleted
            stats["successful_jobs_deleted"] = deleted

        # Clean up old failed jobs
        failed_count = self._count_jobs_by_status(JobStatus.FAILURE, cutoff_failed)
        stats["failed_jobs_deleted"] = failed_count

        if not dry_run and failed_count > 0:
            deleted = self._delete_jobs_by_status(JobStatus.FAILURE, cutoff_failed, batch_size)
            total_deleted += deleted
            stats["failed_jobs_deleted"] = deleted

        # Clean up old revoked jobs (same retention as successful)
        revoked_count = self._count_jobs_by_status(JobStatus.REVOKED, cutoff_success)
        stats["revoked_jobs_deleted"] = revoked_count

        if not dry_run and revoked_count > 0:
            deleted = self._delete_jobs_by_status(JobStatus.REVOKED, cutoff_success, batch_size)
            total_deleted += deleted
            stats["revoked_jobs_deleted"] = deleted

        stats["total_deleted"] = total_deleted

        return stats

    def _count_jobs_by_status(self, status: JobStatus, cutoff_date: datetime) -> int:
        """Count jobs with given status older than cutoff date."""
        return (
            self.db.query(Job)
            .filter(
                Job.status == status,
                Job.finished_at < cutoff_date,
            )
            .count()
        )

    def _delete_jobs_by_status(
        self,
        status: JobStatus,
        cutoff_date: datetime,
        batch_size: int,
    ) -> int:
        """Delete jobs with given status older than cutoff date in batches."""
        total_deleted = 0

        while True:
            # Get a batch of job IDs to delete
            job_ids = (
                self.db.query(Job.id)
                .filter(
                    Job.status == status,
                    Job.finished_at < cutoff_date,
                )
                .limit(batch_size)
                .all()
            )

            job_ids = [jid[0] for jid in job_ids]  # Extract UUIDs from results

            if not job_ids:
                break

            # Delete the batch (idempotent — re-running deletes the same ids is
            # a no-op), retrying transient DB failures at the batch boundary.
            total_deleted += run_with_db_retry(self._delete_job_batch, job_ids)

            # If we got fewer than batch_size, we're done
            if len(job_ids) < batch_size:
                break

        return total_deleted

    def _delete_job_batch(self, job_ids) -> int:
        """Delete one batch of job ids. Idempotent; returns the batch size."""
        self.db.rollback()  # clear any aborted transaction from a prior attempt
        self.db.execute(delete(Job).where(Job.id.in_(job_ids)))
        self.db.commit()
        return len(job_ids)

    def get_retention_stats(self) -> dict:
        """Get current job retention statistics without deleting anything."""
        now = datetime.now(UTC)

        # Count jobs by status and age
        stats = {
            "total_jobs": self.db.query(Job).count(),
            "by_status": {},
            "by_age": {
                "older_than_30_days": 0,
                "older_than_60_days": 0,
                "older_than_90_days": 0,
            },
        }

        # Count by status
        for status in JobStatus:
            count = self.db.query(Job).filter(Job.status == status).count()
            stats["by_status"][status.value] = count

        # Count by age
        cutoff_30 = now - timedelta(days=30)
        cutoff_60 = now - timedelta(days=60)
        cutoff_90 = now - timedelta(days=90)

        stats["by_age"]["older_than_30_days"] = self.db.query(Job).filter(Job.created_at < cutoff_30).count()
        stats["by_age"]["older_than_60_days"] = self.db.query(Job).filter(Job.created_at < cutoff_60).count()
        stats["by_age"]["older_than_90_days"] = self.db.query(Job).filter(Job.created_at < cutoff_90).count()

        return stats
