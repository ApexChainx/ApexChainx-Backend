"""Rate limiting for event fan-out (issue #741).

A subscriber registers for an event and then receives one delivery per
emission.  The number of emissions is not under the subscriber's control: an
outage affecting forty devices emits forty ``sla.violation`` events, and a
recomputed SLA emits them again on every recompute.

Nothing bounded that, so a single subscriber could be turned into an
amplifier for the whole fan-out.  This module gives every subscriber a budget
of events per window and refuses the ones that are over budget.

The counter lives in Redis when Redis is available, so the budget is shared
by every worker.  When Redis is down the limiter falls back to an in-process
counter: the budget is then per worker rather than global, which is weaker
but still bounds the fan-out instead of removing the bound entirely.
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.utils.logging import get_structured_logger

logger = get_structured_logger(__name__)

KEY_PREFIX = "event_listener:rate_limit"


class EventRateLimiter:
    """Fixed-window limiter over the events delivered to each subscriber."""

    def __init__(
        self,
        redis_client: Redis | None = None,
        *,
        limit: int | None = None,
        window_seconds: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._limit = limit if limit is not None else settings.EVENT_LISTENER_RATE_LIMIT_PER_SUBSCRIBER
        self._window = (
            window_seconds if window_seconds is not None else settings.EVENT_LISTENER_RATE_LIMIT_WINDOW_SECONDS
        )
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _key(subscriber_id: str) -> str:
        return f"{KEY_PREFIX}:{subscriber_id}"

    def _allow_local(self, subscriber_id: str) -> bool:
        """Sliding-window check against the in-process counter."""
        now = time.monotonic()
        window_start = now - self._window
        with self._lock:
            hits = [stamp for stamp in self._hits[subscriber_id] if stamp > window_start]
            if len(hits) >= self._limit:
                self._hits[subscriber_id] = hits
                return False
            hits.append(now)
            self._hits[subscriber_id] = hits
            return True

    def _allow_redis(self, subscriber_id: str) -> bool:
        """Fixed-window check against the shared Redis counter."""
        key = self._key(subscriber_id)
        count = int(self._redis.incr(key))
        # The TTL is armed when the key is created so the window really is a
        # window.  Re-arming it on every hit would turn the fixed window into
        # a sliding one that never resets while the subscriber keeps sending.
        if count == 1 or self._redis.ttl(key) < 0:
            self._redis.expire(key, self._window)
        return count <= self._limit

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def limit(self) -> int:
        """The number of events a subscriber may receive per window."""
        return self._limit

    @property
    def window_seconds(self) -> int:
        """The length of the rate-limit window in seconds."""
        return self._window

    def allow(self, subscriber_id: str) -> bool:
        """Consume one unit of ``subscriber_id``'s budget.

        Returns:
            ``True`` when the subscriber may receive the event, ``False`` when
            it is over budget.
        """
        if self._redis is not None:
            try:
                return self._allow_redis(subscriber_id)
            except (RedisError, OSError) as exc:
                logger.warning(
                    "event_listener_rate_limiter_redis_unavailable",
                    event_name="event_listener_rate_limiter_redis_unavailable",
                    subscriber_id=subscriber_id,
                    error=str(exc),
                )
                # Fall through to the in-process limiter: a weaker but still
                # real bound is better than no bound at all.
        return self._allow_local(subscriber_id)

    def retry_after_seconds(self, subscriber_id: str) -> int:
        """Seconds until the subscriber's budget is replenished."""
        if self._redis is not None:
            try:
                ttl = int(self._redis.ttl(self._key(subscriber_id)))
                if ttl > 0:
                    return ttl
            except (RedisError, OSError) as exc:
                logger.warning(
                    "event_listener_rate_limiter_redis_unavailable",
                    event_name="event_listener_rate_limiter_redis_unavailable",
                    subscriber_id=subscriber_id,
                    error=str(exc),
                )
        with self._lock:
            hits = self._hits.get(subscriber_id) or []
            if not hits:
                return 0
            oldest = min(hits)
            remaining = self._window - (time.monotonic() - oldest)
            return max(0, int(remaining) + 1)

    def cull_expired(self, now: float | None = None) -> int:
        """Drop subscribers whose every recorded hit is older than the window.

        The in-process map is keyed by subscriber id, so a subscriber that
        stops receiving events would otherwise keep its history for the
        lifetime of the process.

        Returns:
            The number of subscribers removed.
        """
        current = now if now is not None else time.monotonic()
        window_start = current - self._window
        with self._lock:
            expired = [
                subscriber_id for subscriber_id, hits in self._hits.items() if not hits or hits[-1] <= window_start
            ]
            for subscriber_id in expired:
                del self._hits[subscriber_id]
        return len(expired)
