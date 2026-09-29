import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base_class import Base


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    REVOKED = "revoked"


class JobState(str, enum.Enum):
    """Intended job state model, mapped from Celery task states at the API boundary.

    Celery can report any of PENDING / STARTED / RETRY / REVOKED / SUCCESS /
    FAILURE, plus framework-only scheduling states (RECEIVED, SCHEDULED,
    REJECTED) that a worker may surface depending on configuration
    (``task_track_started``, eager mode, task reject on loss, etc.).  The job
    API must never leak those strings verbatim: every Celery state is mapped
    to one of the five persisted :class:`JobStatus` values (or kept unchanged
    for unknown states) so consumers only ever see states this service owns.

    Mapping (issue #571):

    - ``SUCCESS``   -> SUCCESS (terminal)
    - ``FAILURE``   -> FAILURE (terminal)
    - ``REVOKED``   -> REVOKED (terminal)
    - ``STARTED``   -> STARTED (running)
    - ``PENDING``   -> PENDING (waiting for a worker)
    - ``RETRY``     -> PENDING (Celery re-enqueued the task; it is queued again)
    - ``RECEIVED``  -> PENDING (worker got the message, not yet executing)
    - ``SCHEDULED`` -> PENDING (worker has it scheduled with ETA)
    - ``REJECTED``  -> FAILURE (worker rejected/requeued-as-dead)

    Unknown/None states keep the DB status unchanged rather than guessing.
    """

    SUCCESS = "success"
    FAILURE = "failure"
    REVOKED = "revoked"
    STARTED = "started"
    PENDING = "pending"
    RETRY = "retry"
    RECEIVED = "received"
    SCHEDULED = "scheduled"
    REJECTED = "rejected"

    @classmethod
    def to_job_status(cls, celery_state: str | None) -> JobStatus | None:
        """Map a raw Celery state string to the persisted JobStatus.

        Returns ``None`` for unknown/missing states so callers can keep the
        stored status instead of inventing one.
        """
        mapped: dict[str, JobStatus] = {
            # Celery state name -> persisted JobStatus. Keys are the upper-case
            # Celery state names (what ``AsyncResult.state`` reports), not the
            # enum's lower-case values.
            "SUCCESS": JobStatus.SUCCESS,
            "FAILURE": JobStatus.FAILURE,
            "REVOKED": JobStatus.REVOKED,
            "STARTED": JobStatus.STARTED,
            "PENDING": JobStatus.PENDING,
            # Celery re-enqueued the task after a retry — it is queued again.
            "RETRY": JobStatus.PENDING,
            # Framework scheduling states between reception and execution.
            "RECEIVED": JobStatus.PENDING,
            "SCHEDULED": JobStatus.PENDING,
            # The worker rejected the task (e.g. requeue on worker loss).
            "REJECTED": JobStatus.FAILURE,
        }
        if not celery_state:
            return None
        return mapped.get(celery_state.strip().upper())


class JobType(str, enum.Enum):
    SLA_COMPUTATION = "sla_computation"
    WEBHOOK_DISPATCH = "webhook_dispatch"
    BULK_SLA_COMPUTATION = "bulk_sla_computation"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    celery_task_id = Column(String(255), unique=True, nullable=False, index=True)
    job_type = Column(SAEnum(JobType), nullable=False)
    status = Column(SAEnum(JobStatus), default=JobStatus.PENDING, nullable=False)
    payload = Column(JSONB, nullable=True)  # JSON input params
    result = Column(JSONB, nullable=True)  # JSON result
    error = Column(Text, nullable=True)
    progress = Column(Float, default=0.0)  # 0.0 – 100.0
    progress_details = Column(JSON, nullable=True)  # Structured progress information
    partial_results = Column(JSON, nullable=True)  # Partial results for bulk operations
    per_item_errors = Column(JSON, nullable=True)  # Per-item error tracking
    # BE-041: Retry tracking
    retry_count = Column(Integer, default=0, nullable=False)  # Number of times job has been retried
    max_retries = Column(Integer, default=3, nullable=False)  # Maximum allowed retries for this job
    last_retried_at = Column(DateTime, nullable=True)  # When the job was last retried
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)
