"""Transient DB error retry policy using tenacity for issue #34."""

import logging

from sqlalchemy.exc import DisconnectionError, OperationalError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.exceptions import ApexTransientError

logger = logging.getLogger(__name__)

db_retry_policy = retry(
    retry=retry_if_exception_type((OperationalError, DisconnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)


def run_with_db_retry(fn, *args, **kwargs):
    """Run an idempotent DB operation with transient-error retry.

    Each retry re-runs the whole callable, so it must be self-contained and
    idempotent (e.g. re-executing a batch delete of the same ids is a no-op).
    Exhausted retries surface as ``ApexTransientError`` so callers can handle
    them uniformly instead of receiving a raw 500.
    """
    try:
        return db_retry_policy(fn)(*args, **kwargs)
    except (OperationalError, DisconnectionError) as exc:
        logger.warning("Transient database error persisted after retries: %s", exc)
        raise ApexTransientError(detail=f"Transient database error: {exc}") from exc
