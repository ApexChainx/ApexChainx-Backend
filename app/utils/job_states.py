"""Closed Celery → JobStatus mapping for the jobs API boundary (#571).

The jobs API used to pass Celery's state strings through loosely: every
``AsyncResult.state`` value was copied into a local lookup that only knew five
states, so anything unexpected (a custom worker state, a typo after a framework
upgrade) silently froze the job's stored status in place. The boundary now
funnels every Celery state through this one explicit, unit-tested table and
only ever emits ``JobStatus`` members — the state model documented in
docs/API.md ("Jobs Endpoints").
"""

from __future__ import annotations

from app.models.job import JobStatus
from app.utils.logging import get_structured_logger

logger = get_structured_logger("job_states")

# Every Celery state the jobs API recognises, mapped to the JobStatus the API
# reports. Celery RETRY has no dedicated JobStatus member: a retried task is
# actively waiting for (or running) its next attempt, which the API expresses
# as ``started`` while ``retry_count``/``last_retried_at`` carry the retry
# distinction.
CELERY_STATE_TO_JOB_STATUS: dict[str, JobStatus] = {
    "PENDING": JobStatus.PENDING,
    "STARTED": JobStatus.STARTED,
    "RETRY": JobStatus.STARTED,
    "SUCCESS": JobStatus.SUCCESS,
    "FAILURE": JobStatus.FAILURE,
    "REVOKED": JobStatus.REVOKED,
}


def celery_state_to_job_status(celery_state: object, fallback: JobStatus) -> JobStatus:
    """Map a Celery state string to the JobStatus the API reports.

    Unknown or custom states never leak through to responses: the job keeps
    its stored status (which is already a valid JobStatus) and the deviation
    is logged for triage.
    """
    mapped = CELERY_STATE_TO_JOB_STATUS.get(str(celery_state).upper())
    if mapped is None:
        logger.debug(
            "Unmapped Celery state; keeping stored job status",
            celery_state=str(celery_state),
            fallback=fallback.value,
        )
        return fallback
    return mapped
