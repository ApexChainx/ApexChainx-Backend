import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Enum as SAEnum, Float, JSON
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.db.base_class import Base


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    REVOKED = "revoked"


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
    payload = Column(Text, nullable=True)        # JSON-encoded input params
    result = Column(Text, nullable=True)         # JSON-encoded result
    error = Column(Text, nullable=True)
    progress = Column(Float, default=0.0)        # 0.0 – 100.0
    progress_details = Column(JSON, nullable=True)  # Structured progress information
    partial_results = Column(JSON, nullable=True)   # Partial results for bulk operations
    per_item_errors = Column(JSON, nullable=True)   # Per-item error tracking
    # BE-041: Retry tracking
    retry_count = Column(Integer, default=0, nullable=False)  # Number of times job has been retried
    max_retries = Column(Integer, default=3, nullable=False)  # Maximum allowed retries for this job
    last_retried_at = Column(DateTime, nullable=True)  # When the job was last retried
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
