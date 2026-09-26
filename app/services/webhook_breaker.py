from __future__ import annotations

import logging
import threading
import time
from enum import Enum

from app.core.config import settings
from app.services.metrics import set_gauge

logger = logging.getLogger(__name__)

# Cross-worker visibility for the OPEN state (issue #293): a minimal shared
# marker so a host tripped by one gunicorn worker is also blocked by the
# others, instead of relying purely on in-process memory. This intentionally
# does not attempt the full Lua-atomic, failure-window-in-Redis design (see
# app/core/rate_limiter.py for that pattern) or fix half-open probe
# multiplication / restart-survival beyond the TTL below — those remain
# tracked in #293. Fails open (falls back to in-process state) if Redis is
# unavailable, matching the existing rate limiter's fail-open behavior.
try:
    import redis as _redis_sync

    _redis_client: object | None = _redis_sync.Redis.from_url(
        settings.CELERY_BROKER_URL, socket_timeout=0.2, socket_connect_timeout=0.2
    )
except Exception:  # pragma: no cover - best-effort shared state
    _redis_client = None


class BreakerState(Enum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


class CircuitBreaker:
    def __init__(
        self,
        fail_threshold: int = 10,
        window_seconds: int = 300,
        reset_seconds: int = 600,
    ) -> None:
        self._fail_threshold = fail_threshold
        self._window_seconds = window_seconds
        self._reset_seconds = reset_seconds
        self._state: dict[str, BreakerState] = {}
        self._failures: dict[str, list[float]] = {}
        self._open_since: dict[str, float] = {}
        self._probe_sent: dict[str, bool] = {}
        self._lock = threading.Lock()

    def _host_key(self, url: str) -> str:
        from urllib.parse import urlparse

        try:
            return urlparse(str(url)).hostname or str(url)
        except Exception:
            return str(url)

    def _record_failure(self, host: str) -> None:
        now = time.time()
        cutoff = now - self._window_seconds
        self._failures.setdefault(host, [])
        self._failures[host] = [t for t in self._failures[host] if t > cutoff]
        self._failures[host].append(now)

        if len(self._failures[host]) >= self._fail_threshold:
            self._state[host] = BreakerState.OPEN
            self._open_since[host] = now
            self._probe_sent[host] = False
            self._update_metric(host)
            if _redis_client is not None:
                try:
                    _redis_client.setex(f"webhook_breaker:open:{host}", self._reset_seconds, "1")
                except Exception:
                    logger.warning("Webhook breaker: failed to publish OPEN state for %s to Redis.", host)

    def _record_success(self, host: str) -> None:
        self._failures.pop(host, None)
        self._state[host] = BreakerState.CLOSED
        self._open_since.pop(host, None)
        self._probe_sent.pop(host, None)
        self._update_metric(host)
        if _redis_client is not None:
            try:
                _redis_client.delete(f"webhook_breaker:open:{host}")
            except Exception:
                logger.warning("Webhook breaker: failed to clear OPEN state for %s in Redis.", host)

    def _update_metric(self, host: str) -> None:
        state = self._state.get(host, BreakerState.CLOSED)
        value = {"closed": 0, "half_open": 1, "open": 2}[state.value]
        set_gauge("webhook_breaker_state", value, {"host": host})

    def allow_request(self, url: str) -> bool:
        host = self._host_key(url)
        with self._lock:
            state = self._state.get(host, BreakerState.CLOSED)

            if state == BreakerState.CLOSED and _redis_client is not None:
                try:
                    if _redis_client.exists(f"webhook_breaker:open:{host}"):
                        # Another worker tripped this host; adopt OPEN locally.
                        self._state[host] = BreakerState.OPEN
                        self._open_since[host] = time.time()
                        state = BreakerState.OPEN
                        self._update_metric(host)
                except Exception:
                    logger.warning("Webhook breaker: failed to check shared OPEN state for %s.", host)

            if state == BreakerState.OPEN:
                now = time.time()
                if now - self._open_since.get(host, 0) >= self._reset_seconds:
                    self._state[host] = BreakerState.HALF_OPEN
                    self._update_metric(host)
                else:
                    return False

            state = self._state.get(host, BreakerState.CLOSED)
            if state == BreakerState.HALF_OPEN:
                if self._probe_sent.get(host, False):
                    return False
                self._probe_sent[host] = True
                return True

            return True

    def on_success(self, url: str) -> None:
        host = self._host_key(url)
        with self._lock:
            self._record_success(host)

    def on_failure(self, url: str) -> None:
        host = self._host_key(url)
        with self._lock:
            state = self._state.get(host, BreakerState.CLOSED)
            if state == BreakerState.HALF_OPEN:
                self._state[host] = BreakerState.OPEN
                self._open_since[host] = time.time()
                self._probe_sent[host] = False
                self._update_metric(host)
                return
            self._record_failure(host)

    def get_state(self, url: str) -> str:
        host = self._host_key(url)
        return self._state.get(host, BreakerState.CLOSED).value

    def reset(self, url: str) -> None:
        host = self._host_key(url)
        with self._lock:
            self._state.pop(host, None)
            self._failures.pop(host, None)
            self._open_since.pop(host, None)
            self._probe_sent.pop(host, None)


breaker = CircuitBreaker(
    fail_threshold=getattr(settings, "WEBHOOK_BREAKER_FAIL_THRESHOLD", 10),
    window_seconds=getattr(settings, "WEBHOOK_BREAKER_WINDOW_SECONDS", 300),
    reset_seconds=getattr(settings, "WEBHOOK_BREAKER_RESET_SECONDS", 600),
)
