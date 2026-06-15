import json
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fastapi import Request

from app.db.session import get_db
from app.models.job import Job, JobStatus, JobType
from app.services.audit_log import audit_log
from app.services.metrics import increment_counter, timer
from app.tasks.celery_app import celery_app
from app.tasks.sla_tasks import enqueue_sla_computation, enqueue_bulk_sla_computation
from app.tasks.webhook_tasks import dispatch_webhook_delivery
from app.utils.correlation import get_correlation_id
from app.utils.logging import get_structured_logger
from app.core.security import require_engineer, require_admin
from app.services.job_cleanup import JobCleanupService

logger = get_structured_logger("jobs_api")

router = APIRouter(prefix="/jobs", tags=["Jobs"])


# --------------------------------------------------------------------------- #
# Schemas                                                                      #
# --------------------------------------------------------------------------- #

class SLAJobRequest(BaseModel):
    device_id: str
    period: str  # e.g. "2024-01", "2024-Q1"


class BulkSLAJobRequest(BaseModel):
    device_ids: List[str]
    period: str


class JobResponse(BaseModel):
    id: UUID
    celery_task_id: str
    job_type: JobType
    status: JobStatus
    progress: float
    progress_details: Optional[dict] = None
    partial_results: Optional[dict] = None
    per_item_errors: Optional[dict] = None
    payload: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    # BE-041: Retry metadata
    retry_count: int = 0
    max_retries: int = 3
    last_retried_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str

    model_config = {"from_attributes": True}


# BE-042: Job cleanup schemas
class JobRetentionStatsResponse(BaseModel):
    """Current job retention statistics."""
    total_jobs: int
    by_status: dict
    by_age: dict


class JobCleanupRequest(BaseModel):
    """Request parameters for job cleanup."""
    successful_retention_days: Optional[int] = None
    failed_retention_days: Optional[int] = None
    dry_run: bool = False


class JobCleanupResponse(BaseModel):
    """Response from job cleanup operation."""
    successful_jobs_deleted: int
    failed_jobs_deleted: int
    revoked_jobs_deleted: int
    total_deleted: int
    cutoff_successful: str
    cutoff_failed: str
    dry_run: bool


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _serialize_job(job: Job) -> JobResponse:
    def _parse(val):
        if val is None:
            return None
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val

    return JobResponse(
        id=job.id,
        celery_task_id=job.celery_task_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        progress_details=job.progress_details,
        partial_results=job.partial_results,
        per_item_errors=job.per_item_errors,
        payload=_parse(job.payload),
        result=_parse(job.result),
        error=job.error,
        # BE-041: Include retry metadata
        retry_count=job.retry_count,
        max_retries=job.max_retries,
        last_retried_at=job.last_retried_at.isoformat() if job.last_retried_at else None,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        created_at=job.created_at.isoformat(),
    )


def _get_job_or_404(db: Session, job_id: UUID) -> Job:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return job


def _sync_job_status_from_celery(db: Session, job: Job) -> Job:
    """
    Pull the latest status from Celery backend and update the DB record
    for jobs that haven't reached a terminal state yet.
    """
    if job.status in (JobStatus.SUCCESS, JobStatus.FAILURE, JobStatus.REVOKED):
        return job

    task_result: AsyncResult = AsyncResult(job.celery_task_id, app=celery_app)
    celery_state = task_result.state  # PENDING, STARTED, SUCCESS, FAILURE, REVOKED

    state_map = {
        "PENDING": JobStatus.PENDING,
        "STARTED": JobStatus.STARTED,
        "SUCCESS": JobStatus.SUCCESS,
        "FAILURE": JobStatus.FAILURE,
        "REVOKED": JobStatus.REVOKED,
    }

    new_status = state_map.get(celery_state, job.status)
    if new_status != job.status:
        job.status = new_status
        db.commit()
        db.refresh(job)

    return job


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.post(
    "/sla-computation",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_sla_computation(payload: SLAJobRequest, request: Request, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    """
    Enqueue an async SLA computation job for a single device.
    Returns immediately with a job record for status polling.
    """
    correlation_id = get_correlation_id()
    
    logger.info(
        "Submitting SLA computation job",
        device_id=payload.device_id,
        period=payload.period,
        correlation_id=correlation_id
    )
    
    with timer("job_submission_duration", {"job_type": "sla_computation"}):
        increment_counter("jobs_submitted", tags={"job_type": "sla_computation"})
        job = enqueue_sla_computation(
            db, 
            device_id=payload.device_id, 
            period=payload.period,
            correlation_id=correlation_id
        )
        
        logger.info(
            "SLA computation job submitted",
            job_id=str(job.id),
            celery_task_id=job.celery_task_id,
            correlation_id=correlation_id
        )
        
        return _serialize_job(job)


@router.post(
    "/sla-computation/bulk",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_bulk_sla_computation(payload: BulkSLAJobRequest, request: Request, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    """
    Enqueue an async bulk SLA computation job for multiple devices.
    Returns immediately with a job record for status polling.
    """
    correlation_id = get_correlation_id()
    
    if not payload.device_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="device_ids must not be empty.",
        )
    
    logger.info(
        "Submitting bulk SLA computation job",
        device_count=len(payload.device_ids),
        period=payload.period,
        correlation_id=correlation_id
    )
    
    with timer("job_submission_duration", {"job_type": "bulk_sla_computation"}):
        increment_counter("jobs_submitted", tags={"job_type": "bulk_sla_computation"})
        increment_counter("bulk_job_devices_submitted", value=len(payload.device_ids))
        job = enqueue_bulk_sla_computation(
            db, 
            device_ids=payload.device_ids, 
            period=payload.period,
            correlation_id=correlation_id
        )
        
        logger.info(
            "Bulk SLA computation job submitted",
            job_id=str(job.id),
            celery_task_id=job.celery_task_id,
            device_count=len(payload.device_ids),
            correlation_id=correlation_id
        )
        
        return _serialize_job(job)


@router.get("", response_model=List[JobResponse])
def list_jobs(
    job_type: Optional[JobType] = Query(None),
    status_filter: Optional[JobStatus] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """List all jobs with optional filters."""
    query = db.query(Job).order_by(Job.created_at.desc())
    if job_type is not None:
        query = query.filter(Job.job_type == job_type)
    if status_filter is not None:
        query = query.filter(Job.status == status_filter)
    return [_serialize_job(j) for j in query.limit(limit).all()]


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    """
    Get a single job's status. Syncs status from Celery backend
    for in-progress jobs before responding.
    """
    job = _get_job_or_404(db, job_id)
    job = _sync_job_status_from_celery(db, job)
    return _serialize_job(job)


class JobProgressResponse(BaseModel):
    id: UUID
    status: JobStatus
    progress: float
    progress_details: Optional[dict] = None
    partial_results: Optional[dict] = None
    per_item_errors: Optional[dict] = None

    model_config = {"from_attributes": True}


@router.get("/{job_id}/progress", response_model=JobProgressResponse)
def get_job_progress(job_id: UUID, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    """
    Lightweight polling endpoint returning only progress fields.
    Syncs status from Celery for in-progress jobs before responding.
    """
    job = _get_job_or_404(db, job_id)
    job = _sync_job_status_from_celery(db, job)
    return JobProgressResponse(
        id=job.id,
        status=job.status,
        progress=job.progress,
        progress_details=job.progress_details,
        partial_results=job.partial_results,
        per_item_errors=job.per_item_errors,
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_job(job_id: UUID, current_user=Depends(require_admin), db: Session = Depends(get_db)):
    """
    Revoke a pending or running Celery task and mark the job as REVOKED.
    Does not interrupt already-executing tasks unless terminate=True is set.
    """
    job = _get_job_or_404(db, job_id)
    if job.status in (JobStatus.SUCCESS, JobStatus.FAILURE, JobStatus.REVOKED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel a job with status '{job.status}'.",
        )

    # Log job revocation before performing the action
    audit_log.log_event(
        db,
        event_type="job_revoked",
        details={
            "job_id": str(job.id),
            "celery_task_id": job.celery_task_id,
            "job_type": job.job_type.value,
            "previous_status": job.status.value,
            "payload": job.payload
        }
    )

    increment_counter("jobs_cancelled", tags={"job_type": job.job_type.value})
    
    celery_app.control.revoke(job.celery_task_id, terminate=False)
    job.status = JobStatus.REVOKED
    db.commit()


# BE-041: Job retry endpoint

class JobRetryResponse(BaseModel):
    """Response from job retry operation."""
    id: UUID
    celery_task_id: str
    job_type: JobType
    status: JobStatus
    retry_count: int
    max_retries: int
    message: str

    model_config = {"from_attributes": True}


@router.post(
    "/{job_id}/retry",
    response_model=JobRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job(
    job_id: UUID,
    request: Request,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Retry a failed or revoked job.
    
    BE-041: Allows authorized users to intentionally retry eligible failed jobs.
    
    Retry Policy:
    - Only FAILED or REVOKED jobs can be retried
    - Maximum retries per job: configurable via max_retries field (default: 3)
    - Each retry creates a new Celery task with the original payload
    - Retry attempts are tracked and audited
    - Jobs that exceed max_retries are permanently marked as failed
    
    Returns:
        202 Accepted with new job status and incremented retry count
        400 Bad Request if job is not eligible for retry
        404 Not Found if job doesn't exist
    """
    correlation_id = get_correlation_id()
    job = _get_job_or_404(db, job_id)
    
    # Validate job is eligible for retry
    if job.status not in (JobStatus.FAILURE, JobStatus.REVOKED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry job with status '{job.status.value}'. Only FAILED or REVOKED jobs can be retried.",
        )
    
    # Check retry limit
    if job.retry_count >= job.max_retries:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job has exceeded maximum retry limit ({job.max_retries}). Current retry count: {job.retry_count}",
        )
    
    # Log retry attempt before performing the action
    audit_log.log_event(
        db,
        event_type="job_retry_initiated",
        details={
            "job_id": str(job.id),
            "original_celery_task_id": job.celery_task_id,
            "job_type": job.job_type.value,
            "previous_status": job.status.value,
            "retry_count": job.retry_count + 1,  # Will be incremented
            "max_retries": job.max_retries,
            "payload": job.payload,
            "previous_error": job.error,
            "correlation_id": correlation_id,
            "initiated_by": getattr(current_user, 'email', 'unknown'),
        }
    )
    
    logger.info(
        "Retrying job",
        job_id=str(job.id),
        job_type=job.job_type.value,
        retry_count=job.retry_count + 1,
        max_retries=job.max_retries,
        correlation_id=correlation_id
    )
    
    # Increment retry count and update status
    job.retry_count += 1
    job.last_retried_at = datetime.utcnow()
    job.error = None  # Clear previous error
    job.status = JobStatus.PENDING
    job.progress = 0.0
    job.started_at = None
    job.finished_at = None
    
    # Re-enqueue the job based on its type
    try:
        payload = json.loads(job.payload) if job.payload else {}
        
        if job.job_type == JobType.SLA_COMPUTATION:
            new_task = enqueue_sla_computation(
                db,
                device_id=payload.get("device_id", ""),
                period=payload.get("period", ""),
                correlation_id=correlation_id
            )
        elif job.job_type == JobType.BULK_SLA_COMPUTATION:
            new_task = enqueue_bulk_sla_computation(
                db,
                device_ids=payload.get("device_ids", []),
                period=payload.get("period", ""),
                correlation_id=correlation_id
            )
        elif job.job_type == JobType.WEBHOOK_DISPATCH:
            # For webhook jobs, re-dispatch with the original payload
            from app.tasks.webhook_tasks import dispatch_webhook_delivery
            task_result = dispatch_webhook_event.delay(payload)
            job.celery_task_id = task_result.id
            db.commit()
            db.refresh(job)
            
            return JobRetryResponse(
                id=job.id,
                celery_task_id=job.celery_task_id,
                job_type=job.job_type,
                status=job.status,
                retry_count=job.retry_count,
                max_retries=job.max_retries,
                message=f"Job retry #{job.retry_count} initiated successfully",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported job type for retry: {job.job_type.value}",
            )
        
        # Update job with new Celery task ID
        job.celery_task_id = new_task.celery_task_id
        db.commit()
        db.refresh(job)
        
        logger.info(
            "Job retry enqueued successfully",
            job_id=str(job.id),
            new_celery_task_id=job.celery_task_id,
            retry_count=job.retry_count,
            correlation_id=correlation_id
        )
        
        return JobRetryResponse(
            id=job.id,
            celery_task_id=job.celery_task_id,
            job_type=job.job_type,
            status=job.status,
            retry_count=job.retry_count,
            max_retries=job.max_retries,
            message=f"Job retry #{job.retry_count} initiated successfully",
        )
        
    except Exception as e:
        db.rollback()
        logger.error(
            "Failed to retry job",
            job_id=str(job.id),
            error=str(e),
            correlation_id=correlation_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retry job: {str(e)}",
        )


# BE-042: Job retention and cleanup endpoints

@router.get("/retention-stats", response_model=JobRetentionStatsResponse)
def get_job_retention_stats(current_user=Depends(require_admin), db: Session = Depends(get_db)):
    """Get current job retention statistics without deleting anything.
    
    BE-042: Provides visibility into job storage usage and aging.
    """
    cleanup_service = JobCleanupService(db)
    return cleanup_service.get_retention_stats()


@router.post("/cleanup", response_model=JobCleanupResponse)
def cleanup_old_jobs(
    payload: JobCleanupRequest,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Clean up old completed and failed jobs based on retention policy.
    
    BE-042: Removes old job records to prevent unbounded database growth.
    - Successful/revoked jobs: default 30 day retention
    - Failed jobs: default 90 day retention (preserved longer for debugging)
    
    Use dry_run=True to preview what would be deleted without actually deleting.
    """
    cleanup_service = JobCleanupService(db)
    
    result = cleanup_service.cleanup_old_jobs(
        successful_retention_days=payload.successful_retention_days,
        failed_retention_days=payload.failed_retention_days,
        dry_run=payload.dry_run,
    )
    
    # Log the cleanup operation
    audit_log.log_event(
        db,
        event_type="job_cleanup_executed",
        details={
            "total_deleted": result["total_deleted"],
            "successful_deleted": result["successful_jobs_deleted"],
            "failed_deleted": result["failed_jobs_deleted"],
            "revoked_deleted": result["revoked_jobs_deleted"],
            "dry_run": payload.dry_run,
            "executed_by": getattr(current_user, 'email', 'unknown'),
        }
    )
    
    return JobCleanupResponse(**result)

