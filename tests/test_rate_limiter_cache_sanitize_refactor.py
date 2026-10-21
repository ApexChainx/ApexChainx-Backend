"""Tests for RateLimiter Redis verdict caching, key sanitization and the refactored flow."""

import asyncio
import time

import pytest
from redis.exceptions import RedisError

from app.core import rate_limiter as rl_mod
from app.core.config import settings
from app.core.rate_limiter import (
    EMPTY_RATE_LIMIT_KEY,
    RATE_LIMIT_KEY_MAX_LENGTH,
    RedisRateLimiter,
    SimpleRateLimiter,
    sanitize_rate_limit_key,
)


@pytest.fixture(autouse=True)
def redis_enabled(monkeypatch):
    monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")


@pytest.fixture(autouse=True)
def isolate_shared_state():
    saved = {key: list(hits) for key, hits in SimpleRateLimiter._shared.items()}
    SimpleRateLimiter._shared.clear()
    yield
    SimpleRateLimiter._shared.clear()
    SimpleRateLimiter._shared.update(saved)


class CountingClient:
    """Sync stand-in for a Redis client that returns scripted verdicts."""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = []

    def eval(self, script, numkeys, key, *args):
        self.calls.append(key)
        return self.verdicts.pop(0) if self.verdicts else 0


class AsyncCountingClient(CountingClient):
    async def eval(self, script, numkeys, key, *args):
        return CountingClient.eval(self, script, numkeys, key, *args)


# Sanitization ---------------------------------------------------------------


class TestSanitizeKey:
    def test_safe_keys_are_unchanged(self):
        assert sanitize_rate_limit_key("login_ip_1.2.3.4") == "login_ip_1.2.3.4"
        assert sanitize_rate_limit_key("login_ip_::1") == "login_ip_::1"

    def test_control_and_unsafe_characters_are_replaced(self):
        assert sanitize_rate_limit_key("login\r\nip *?{}") == "login__ip_____"

    def test_whitespace_is_stripped(self):
        assert sanitize_rate_limit_key("  key  ") == "key"

    def test_empty_and_none_collapse_to_anonymous(self):
        assert sanitize_rate_limit_key("") == EMPTY_RATE_LIMIT_KEY
        assert sanitize_rate_limit_key("   ") == EMPTY_RATE_LIMIT_KEY
        assert sanitize_rate_limit_key(None) == EMPTY_RATE_LIMIT_KEY

    def test_non_string_keys_are_stringified(self):
        assert sanitize_rate_limit_key(42) == "42"

    def test_unicode_is_normalised(self):
        # Fullwidth digits normalise to ASCII under NFKC.
        assert sanitize_rate_limit_key("ip_１２３") == "ip_123"

    def test_long_keys_are_hashed_and_bounded(self):
        hashed = sanitize_rate_limit_key("a" * (RATE_LIMIT_KEY_MAX_LENGTH + 1))
        assert len(hashed) == 64
        assert hashed == sanitize_rate_limit_key("a" * (RATE_LIMIT_KEY_MAX_LENGTH + 1))

    def test_simple_limiter_stores_sanitized_key(self):
        limiter = SimpleRateLimiter()
        assert limiter.is_allowed("evil\nkey") is True
        assert "evil_key" in SimpleRateLimiter._shared
        assert "evil\nkey" not in SimpleRateLimiter._shared

    def test_redis_namespace_uses_sanitized_key(self):
        limiter = RedisRateLimiter()
        assert limiter._key_namespace("a\r\nb") == "auth_rate_limiter:a__b"


# Redis verdict caching ------------------------------------------------------


class TestDenyCache:
    def test_denied_key_is_served_from_cache(self):
        limiter = RedisRateLimiter()
        limiter.client = CountingClient([0])

        assert limiter.is_allowed("login_ip_1") is False
        assert limiter.is_allowed("login_ip_1") is False
        assert len(limiter.client.calls) == 1

    def test_allowed_keys_always_hit_redis(self):
        limiter = RedisRateLimiter()
        limiter.client = CountingClient([1, 1, 1])

        for _ in range(3):
            assert limiter.is_allowed("login_ip_2") is True
        assert len(limiter.client.calls) == 3

    def test_cache_expires(self, monkeypatch):
        limiter = RedisRateLimiter()
        limiter.client = CountingClient([0, 1])
        assert limiter.is_allowed("login_ip_3") is False

        future = time.time() + rl_mod.REDIS_DENY_CACHE_SECONDS + 1
        monkeypatch.setattr(rl_mod, "time", lambda: future)
        assert limiter.is_allowed("login_ip_3") is True
        assert "login_ip_3" not in limiter.deny_cache

    def test_cache_ttl_never_exceeds_window(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 1)
        assert RedisRateLimiter()._deny_cache_ttl() == 1

    def test_cache_is_bounded(self, monkeypatch):
        monkeypatch.setattr(rl_mod, "REDIS_DENY_CACHE_MAX_KEYS", 2)
        limiter = RedisRateLimiter()
        limiter.client = CountingClient([0, 0, 0])
        for index in range(3):
            limiter.is_allowed(f"k{index}")
        assert len(limiter.deny_cache) == 2

    def test_clear_cache(self):
        limiter = RedisRateLimiter()
        limiter.client = CountingClient([0])
        limiter.is_allowed("k")
        limiter.clear_cache()
        assert limiter.deny_cache == {}

    def test_async_path_uses_cache(self):
        limiter = RedisRateLimiter()
        limiter.async_client = AsyncCountingClient([0])

        assert asyncio.run(limiter.is_allowed_async("login_ip_4")) is False
        assert asyncio.run(limiter.is_allowed_async("login_ip_4")) is False
        assert len(limiter.async_client.calls) == 1


# Refactored control flow ----------------------------------------------------


class FailingClient:
    def eval(self, *args, **kwargs):
        raise RedisError("down")


class TestRefactoredFlow:
    def test_redis_disabled_uses_fallback(self, monkeypatch):
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", False)
        limiter = RedisRateLimiter()
        limiter.client = FailingClient()
        assert limiter.is_allowed("k") is True
        assert "k" in SimpleRateLimiter._shared

    def test_redis_failure_trips_circuit_and_falls_back(self):
        limiter = RedisRateLimiter()
        limiter.client = FailingClient()
        assert limiter.is_allowed("k") is True
        assert limiter._is_circuit_open()
        assert "k" in SimpleRateLimiter._shared

    def test_db_path_ledger_denial_short_circuits(self, monkeypatch):
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda *a: False)
        limiter = RedisRateLimiter()
        limiter.client = CountingClient([1])
        assert limiter.is_allowed("k", db=object()) is False
        assert limiter.client.calls == []

    def test_db_path_consults_redis_after_ledger(self, monkeypatch):
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda *a: True)
        limiter = RedisRateLimiter()
        limiter.client = CountingClient([0])
        assert limiter.is_allowed("k", db=object()) is False
        assert limiter.client.calls == ["auth_rate_limiter:k"]

    def test_db_path_allows_when_redis_fails(self, monkeypatch):
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda *a: True)
        limiter = RedisRateLimiter()
        limiter.client = FailingClient()
        assert limiter.is_allowed("k", db=object()) is True
        assert "k" not in SimpleRateLimiter._shared

    def test_db_path_passes_sanitized_key_to_ledger(self, monkeypatch):
        seen = []
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda db, key, *a: seen.append(key) or True)
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", False)
        SimpleRateLimiter().is_allowed("bad\x00key", db=object())
        assert seen == ["bad_key"]
