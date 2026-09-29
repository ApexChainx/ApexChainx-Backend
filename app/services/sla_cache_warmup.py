"""SLA cache warm-up (#566).

After a restart (or on the daily cadence) the first wave of SLA reads used to
recompute everything against the database — the classic cold-cache stampede.
The warm-up pre-populates the Redis cache for the busiest devices so early
readers hit warm entries instead of racing into ``compute_device_sla``.

The warm-up writes through ``SLACache.warm_up``, which keys entries exactly
like the read path (``sla:{device_id}:{period}``), so warmed values are
indistinguishable from ones produced during normal traffic.
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.services.sla_cache import SLACache
from app.services.sla_service import SLAOrchestrator, _get_sla_cache

logger = logging.getLogger(__name__)


def warm_sla_cache(db: Session, *, limit: int = 25, periods: list[str] | None = None) -> list[dict]:
    """Precompute and cache SLA results for the most active devices.

    ``periods`` defaults to the current month and the previous month. Devices
    are ranked by outage-row count so the entries most likely to be requested
    first are the ones warmed.
    """
    cache: SLACache | None = _get_sla_cache()
    if cache is None:
        logger.info("SLA cache unavailable; skipping warm-up")
        return []

    if periods:
        window_periods = list(periods)
    else:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        if now.month == 1:
            prev = datetime(now.year - 1, 12, 1, tzinfo=UTC)
        else:
            prev = datetime(now.year, now.month - 1, 1, tzinfo=UTC)
        window_periods = [f"{now.year:04d}-{now.month:02d}", f"{prev.year:04d}-{prev.month:02d}"]

    orchestrator = SLAOrchestrator(db)

    # Rank devices by outage volume inside the warm-up window so the top-N
    # actually reflects the read traffic that follows a restart.
    counts: Counter[str] = Counter()
    for period in window_periods:
        try:
            start_date, end_date = orchestrator.parse_period(period)
        except Exception:  # noqa: BLE001 - bad period strings must not kill warm-up
            continue
        rows = (
            db.query(OutageORM.id)
            .filter(OutageORM.created_at >= start_date)
            .filter(OutageORM.created_at < end_date)
            .all()
        )
        for (device_id,) in rows:
            if device_id:
                counts[device_id] += 1

    top_devices = [device_id for device_id, _ in counts.most_common(limit)]

    warmed: list[dict] = []
    for device_id in top_devices:
        for period in window_periods:
            try:
                result = _compute_for_warmup(db, device_id, period)
            except Exception as exc:  # noqa: BLE001 - one bad device must not abort the warm-up
                logger.warning("SLA cache warm-up failed for device=%s period=%s: %s", device_id, period, exc)
                continue
            warmed.append({"device_id": device_id, "period": period, "result": result})

    cache.warm_up((entry["device_id"], entry["period"], entry["result"]) for entry in warmed)
    logger.info("SLA cache warm-up complete: %d entries", len(warmed))
    return warmed


def _compute_for_warmup(db: Session, device_id: str, period: str) -> dict:
    """Compute a warm-up entry without consulting (or stamping) the cache."""
    from app.services.sla_service import compute_device_sla

    # compute_device_sla checks the cache first and writes through at the end;
    # on a cold cache that is exactly a warm-up write, so reuse it rather than
    # duplicating the computation and serialization logic.
    result = compute_device_sla(db, device_id=device_id, period=period)
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return dict(result)
