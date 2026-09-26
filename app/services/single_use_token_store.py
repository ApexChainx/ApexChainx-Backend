"""Redis-backed single-use token store (issue #267).

Payment provider-callback replay protection previously lived in a
process-local dict, so a captured nonce could be replayed against a
different gunicorn worker (or after a worker restart) within the TTL
window. This module provides a shared, Redis-backed single-use token store
so the "reject duplicates within TTL" guarantee holds across workers,
restarts and hosts.

Failure policy (explicit and tested):

- Redis is the primary store (atomic ``SET NX EX`` per token).
- When Redis is unavailable the store **fails open**: it falls back to a
  bounded in-process map and trips a short circuit breaker before retrying
  Redis. Replay protection is then process-local and weaker, but callbacks
  are never rejected with a 5xx because of a Redis outage — the same
  trade-off the rate limiter and idempotency middleware already make.

The store is deliberately generic (``consume(token) -> bool``) so the same
primitive can back idempotency-key registration or other single-use
guarantees later.
"""

import logging
import time
from threading import Lock

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

TOKEN_KEY_PREFIX = "single_use_token"
CIRCUIT_OPEN_TTL_SECONDS = 30
MAX_FALLBACK_TOKENS = 50_000


class SingleUseTokenStore:
    """Register single-use tokens and detect replays across workers."""

    def __init__(self, ttl_seconds: int = 300, redis_client: Redis | None = None):
        self.ttl_seconds = ttl_seconds
        self.redis = redis_client or Redis.from_url(settings.CELERY_BROKER_URL)
        # Bounded in-process fallback used while Redis is unavailable.
        self._fallback: dict[str, float] = {}
        self._fallback_lock = Lock()
        self._disabled_until: float = 0.0

    # ------------------------------------------------------------------
    # Circuit-breaker state
    # ------------------------------------------------------------------

    def _circuit_open(self) -> bool:
        return time.monotonic() < self._disabled_until

    def _trip_circuit(self) -> None:
        self._disabled_until = time.monotonic() + CIRCUIT_OPEN_TTL_SECONDS
        logger.warning(
            "Single-use token store: Redis unavailable, using in-process fallback for %ds",
            CIRCUIT_OPEN_TTL_SECONDS,
        )

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _consume_fallback(self, token: str) -> bool:
        """Consume *token* in the in-process map; True if it was already used."""
        now = time.monotonic()
        cutoff = now - self.ttl_seconds
        with self._fallback_lock:
            # Bound the fallback: evict stale entries when it grows large.
            if len(self._fallback) >= MAX_FALLBACK_TOKENS:
                stale = [k for k, ts in self._fallback.items() if ts < cutoff]
                for k in stale:
                    del self._fallback[k]
            if token in self._fallback:
                return True
            self._fallback[token] = now
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def consume(self, token: str) -> bool:
        """Register *token* as used and return True if it was already used.

        The first caller for a given token gets ``False`` (newly consumed);
        any later caller within the TTL window gets ``True`` (replay).
        """
        if not settings.CELERY_BROKER_URL.strip():
            return self._consume_fallback(token)

        if not self._circuit_open():
            try:
                key = f"{TOKEN_KEY_PREFIX}:{token}"
                # Atomic SET NX EX: only the first worker can register the
                # token, so the guarantee holds across processes/restarts.
                if self.redis.set(key, "1", nx=True, ex=self.ttl_seconds):
                    return False
                return True
            except (RedisError, OSError) as exc:
                logger.warning("Single-use token store: Redis SET NX failed, falling back: %s", exc)
                self._trip_circuit()
        return self._consume_fallback(token)
