"""Redis read-through cache for event subscriber lists (issue #740).

Resolving the subscribers of an event reads the ``webhooks`` table.  During an
incident the same handful of events is emitted over and over -- once per
affected device -- and every emission repeated the same query and the same
per-row JSON parse of the ``events`` column.

The cache stores the *ids* of the subscribers rather than whole rows:

* a cached ORM instance would go stale the moment the row changes;
* the rows are re-read from the database on a hit, so a webhook that is
  deactivated or deleted stops being delivered to as soon as the TTL expires
  instead of until the entry happens to be evicted.

Cache failures are never fatal.  A degraded Redis degrades to "no cache",
which is exactly the behaviour the listener had before the cache existed.
"""

from __future__ import annotations

import json
import time

from redis import Redis
from redis.exceptions import RedisError

from app.utils.logging import get_structured_logger

logger = get_structured_logger(__name__)

DEFAULT_TTL_SECONDS = 15
KEY_PREFIX = "event_listener:subscribers"

# After this many consecutive Redis failures the cache stops talking to Redis
# for a while.  Without it a Redis outage would add a connection timeout to
# every single event instead of to the first one.
FAILURE_THRESHOLD = 3
FAILURE_COOLDOWN_SECONDS = 30.0


class EventListenerCache:
    """Read-through cache of subscriber ids per event."""

    def __init__(
        self,
        redis_client: Redis,
        ttl: int = DEFAULT_TTL_SECONDS,
        *,
        failure_threshold: int = FAILURE_THRESHOLD,
        failure_cooldown_seconds: float = FAILURE_COOLDOWN_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._ttl = ttl
        self._failure_threshold = failure_threshold
        self._failure_cooldown_seconds = failure_cooldown_seconds
        self._consecutive_failures = 0
        self._disabled_until = 0.0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _key(event: str) -> str:
        return f"{KEY_PREFIX}:{event}"

    def _redis_available(self) -> bool:
        """Whether the cache is allowed to talk to Redis right now."""
        return time.monotonic() >= self._disabled_until

    def _record_failure(self, operation: str, event: str, exc: Exception) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._disabled_until = time.monotonic() + self._failure_cooldown_seconds
            logger.warning(
                "event_listener_cache_disabled",
                event_name="event_listener_cache_disabled",
                operation=operation,
                event=event,
                error=str(exc),
                cooldown_seconds=self._failure_cooldown_seconds,
            )
        else:
            logger.warning(
                "event_listener_cache_failed",
                event_name="event_listener_cache_failed",
                operation=operation,
                event=event,
                error=str(exc),
            )

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_subscriber_ids(self, event: str) -> list[str] | None:
        """Return the cached subscriber ids for ``event``, or ``None`` on a miss."""
        if not self._redis_available():
            return None
        try:
            raw = self._redis.get(self._key(event))
        except (RedisError, OSError) as exc:
            self._record_failure("get", event, exc)
            return None
        self._record_success()

        if not raw:
            return None
        try:
            ids = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            logger.warning(
                "event_listener_cache_corrupt",
                event_name="event_listener_cache_corrupt",
                event=event,
            )
            return None
        if not isinstance(ids, list):
            logger.warning(
                "event_listener_cache_corrupt",
                event_name="event_listener_cache_corrupt",
                event=event,
            )
            return None
        return [str(subscriber_id) for subscriber_id in ids]

    def set_subscriber_ids(self, event: str, subscriber_ids: list[str]) -> None:
        """Cache the subscriber ids for ``event``."""
        if not self._redis_available():
            return
        try:
            payload = json.dumps([str(subscriber_id) for subscriber_id in subscriber_ids])
            self._redis.setex(self._key(event), self._ttl, payload)
        except (RedisError, OSError) as exc:
            self._record_failure("set", event, exc)
            return
        self._record_success()

    def invalidate(self, event: str) -> None:
        """Drop the cached subscriber ids for a single event."""
        if not self._redis_available():
            return
        try:
            self._redis.delete(self._key(event))
        except (RedisError, OSError) as exc:
            self._record_failure("invalidate", event, exc)
            return
        self._record_success()

    def invalidate_all(self) -> int:
        """Drop every cached subscriber list.

        Returns:
            The number of keys that were removed.
        """
        if not self._redis_available():
            return 0
        removed = 0
        try:
            for key in self._redis.scan_iter(match=f"{KEY_PREFIX}:*"):
                removed += int(self._redis.delete(key) or 0)
        except (RedisError, OSError) as exc:
            self._record_failure("invalidate_all", "*", exc)
            return removed
        self._record_success()
        return removed
