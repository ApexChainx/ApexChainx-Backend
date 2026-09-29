"""Periodic pruning of expired OAuth connect-state rows (#568)."""

import logging

from app.services.metrics import increment_counter
from app.services.oauth_session import oauth_state_repo
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.oauth_tasks.prune_expired_oauth_states")
def prune_expired_oauth_states() -> dict:
    """Beat task: delete stale OAuth state rows that outlived their expires_at.

    Redis TTLs are the primary expiry mechanism, but rows that lost their TTL
    (or whose TTL a future backend ignores) would otherwise live forever. The
    read path also expires lazily; this sweep is the belt to that suspenders.
    """
    deleted = oauth_state_repo.prune_expired_states()
    if deleted:
        increment_counter("oauth_state_pruned", value=deleted)
    logger.info("OAuth state pruning complete: %d rows deleted", deleted)
    return {"deleted": deleted}
