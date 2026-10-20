"""Celery entry point for the Stellar block poller (#722-#726).

Beat fires this task on ``BLOCK_POLLER_INTERVAL_SECONDS``. The task is a no-op
unless ``BLOCK_POLLER_ENABLED`` is true so existing deployments do not start
calling Horizon after upgrading.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.block_poller_tasks.poll_stellar_blocks")
def poll_stellar_blocks() -> dict[str, Any]:
    """Run one BlockPoller cycle and return its summary."""
    if not settings.BLOCK_POLLER_ENABLED:
        return {"skipped": True, "reason": "BLOCK_POLLER_ENABLED is false"}

    from app.services.block_poller import build_block_poller

    poller = build_block_poller()
    try:
        result = poller.poll_once()
    finally:
        poller.close()
    payload = result.as_dict()
    payload["skipped"] = False
    if result.error:
        logger.warning("BlockPoller cycle completed with error: %s", result.error)
    return payload
