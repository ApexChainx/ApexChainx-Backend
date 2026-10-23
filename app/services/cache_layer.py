"""Resilient Redis cache operations used by application services.

The cache is an optimization: transient Redis errors become cache misses (or
ignored writes) so callers can continue with their authoritative data source.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)
_SAFE_KEY = re.compile(r"^[A-Za-z0-9:_./-]{1,200}$")


class CacheLayer:
    """Small JSON Redis cache with bounded retries and a failure cooldown."""

    def __init__(
        self,
        redis_client: Redis,
        *,
        namespace: str = "app",
        ttl_seconds: int = 60,
        max_attempts: int = 3,
        backoff_seconds: float = 0.05,
        failure_threshold: int = 3,
        failure_cooldown_seconds: float = 10.0,
    ) -> None:
        if ttl_seconds < 1 or max_attempts < 1 or failure_threshold < 1:
            raise ValueError("ttl_seconds, max_attempts, and failure_threshold must be positive")
        if backoff_seconds < 0 or failure_cooldown_seconds < 0:
            raise ValueError("backoff and cooldown must be non-negative")
        self._redis = redis_client
        self._namespace = self._validate_component(namespace, "namespace")
        self._ttl = ttl_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._failure_threshold = failure_threshold
        self._cooldown = failure_cooldown_seconds
        self._failures = 0
        self._disabled_until = 0.0

    @staticmethod
    def _validate_component(value: str, label: str) -> str:
        if not isinstance(value, str) or not _SAFE_KEY.fullmatch(value):
            raise ValueError(f"invalid cache {label}")
        return value

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{self._validate_component(key, 'key')}"

    def _run(self, operation: str, key: str, callback: Any) -> Any:
        if time.monotonic() < self._disabled_until:
            return None
        for attempt in range(self._max_attempts):
            try:
                result = callback()
                self._failures = 0
                return result
            except (RedisError, OSError) as exc:
                self._failures += 1
                logger.warning(
                    "cache_layer_operation_failed",
                    extra={"operation": operation, "cache_key": key, "attempt": attempt + 1},
                )
                if attempt + 1 < self._max_attempts:
                    time.sleep(self._backoff * (2**attempt))
        if self._failures >= self._failure_threshold:
            self._disabled_until = time.monotonic() + self._cooldown
        return None

    def get(self, key: str) -> Any | None:
        redis_key = self._key(key)
        raw = self._run("get", redis_key, lambda: self._redis.get(redis_key))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            logger.warning("cache_layer_corrupt_value", extra={"cache_key": redis_key})
            self.delete(key)
            return None

    def set(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:
        redis_key = self._key(key)
        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        if ttl < 1:
            raise ValueError("ttl_seconds must be positive")
        try:
            payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            return False
        return bool(self._run("set", redis_key, lambda: self._redis.setex(redis_key, ttl, payload)))

    def delete(self, key: str) -> bool:
        redis_key = self._key(key)
        return bool(self._run("delete", redis_key, lambda: self._redis.delete(redis_key)))
