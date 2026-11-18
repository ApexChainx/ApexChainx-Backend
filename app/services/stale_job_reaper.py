"""Reconciles Celery jobs stuck in STARTED after a worker crash/OOM,
which job_cleanup deliberately skips today.
"""

from datetime import UTC, datetime, timedelta

DEFAULT_HEARTBEAT_TTL = timedelta(minutes=30)


def is_stale_started_job(
    started_at: datetime, now: datetime | None = None, heartbeat_ttl: timedelta = DEFAULT_HEARTBEAT_TTL
) -> bool:
    """A STARTED job older than the heartbeat TTL is considered orphaned."""
    now = now or datetime.now(UTC)
    return now - started_at > heartbeat_ttl


def reap_reason(started_at: datetime) -> str:
    return f"stale STARTED job, no heartbeat since {started_at.isoformat()}"
