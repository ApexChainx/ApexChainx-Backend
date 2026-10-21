"""Boot-time SLA cache warm-up trigger (#566).

The daily beat entry (``warm_sla_cache_task``) keeps entries fresh, but the
stampede risk is worst right after a restart, before any beat tick fires. A
``worker_ready`` signal handler kicks the same task once per worker boot so
the cache is warm before the first read wave arrives.
"""

import logging

from celery.signals import worker_ready

logger = logging.getLogger(__name__)


@worker_ready.connect
def _warm_sla_cache_on_boot(**_kwargs) -> None:
    try:
        from app.tasks.sla_tasks import warm_sla_cache_task

        warm_sla_cache_task.delay()
        logger.info("SLA cache warm-up dispatched on worker boot")
    except Exception:  # noqa: BLE001 - warm-up must never block worker startup
        logger.exception("SLA cache warm-up dispatch failed on worker boot")
