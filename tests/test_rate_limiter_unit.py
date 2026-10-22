"""Unit tests for SimpleRateLimiter and RedisRateLimiter — Issue #750.

These tests exercise the rate limiter components in isolation using mocks
and monkeypatching, with no real Redis dependency.
"""

from __future__ import annotations

import time
from collections import defaultdict
from importlib import reload
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.core.rate_limiter import RedisRateLimiter, SimpleRateLimiter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_simple_rate_limiter_state():
    """Reset the shared class-level state before each test to prevent leakage."""
    SimpleRateLimiter._shared.clear()
    yield
    SimpleRateLimiter._shared.clear()


# ---------------------------------------------------------------------------
# SimpleRateLimiter — allow/deny logic
# ---------------------------------------------------------------------------

class TestSimpleRateLimiterAllowDeny:
    def test_allows_up_to_limit(self, monkeypatch):
        """is_allowed returns True for every request up to the configured limit."""
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_REQUESTS", 5)
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 60)

        limiter = SimpleRateLimiter()
        results = [limiter.is_allowed("unit-test-key") for _ in range(5)]
        assert all(results), "All requests within limit should be allowed"

    def test_rejects_beyond_limit(self, monkeypatch):
        """is_allowed returns False once the limit has been reached."""
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_REQUESTS", 5)
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 60)

        limiter = SimpleRateLimiter()
        for _ in range(5):
            limiter.is_allowed("unit-test-key-exceed")

        assert limiter.is_allowed("unit-test-key-exceed") is False, (
            "Request beyond limit should be rejected"
        )

    def test_different_keys_are_independent(self, monkeypatch):
        """Rate limit state for one key does not affect another."""
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_REQUESTS", 2)
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 60)

        limiter = SimpleRateLimiter()
        # Exhaust key-a
        limiter.is_allowed("key-a")
        limiter.is_allowed("key-a")
        assert limiter.is_allowed("key-a") is False

        # key-b is unaffected
        assert limiter.is_allowed("key-b") is True


# ---------------------------------------------------------------------------
# SimpleRateLimiter — timestamp expiry
# ---------------------------------------------------------------------------

class TestSimpleRateLimiterExpiry:
    def test_old_timestamps_expire_correctly(self, monkeypatch):
        """Timestamps outside the sliding window are removed, allowing new requests."""
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_REQUESTS", 3)
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 10)

        limiter = SimpleRateLimiter()
        key = "expiry-test-key"

        # Insert 3 timestamps that are definitely outside the window (100 s ago)
        old_time = time.time() - 100
        SimpleRateLimiter._shared[key] = [old_time, old_time, old_time]

        # All 3 slots should now be available again because the old ones expired
        assert limiter.is_allowed(key) is True
        assert limiter.is_allowed(key) is True
        assert limiter.is_allowed(key) is True
        # 4th request exceeds limit
        assert limiter.is_allowed(key) is False

    def test_recent_timestamps_are_retained(self, monkeypatch):
        """Timestamps within the window are retained and count toward the limit."""
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_REQUESTS", 3)
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 60)

        limiter = SimpleRateLimiter()
        key = "recent-timestamps-key"

        # Pre-fill 2 recent timestamps
        now = time.time()
        SimpleRateLimiter._shared[key] = [now - 5, now - 3]

        # Only 1 more request should be allowed
        assert limiter.is_allowed(key) is True   # 3rd slot used
        assert limiter.is_allowed(key) is False  # 4th denied

    def test_mock_time_advances_window(self, monkeypatch):
        """Mocking time.time allows precise control over window expiry."""
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_REQUESTS", 2)
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 10)

        key = "mock-time-key"
        fake_now = [1000.0]  # mutable container so the closure can mutate it

        def mock_time():
            return fake_now[0]

        with patch("app.core.rate_limiter.time", side_effect=mock_time):
            limiter = SimpleRateLimiter()
            # At t=1000: use both slots
            assert limiter.is_allowed(key) is True
            assert limiter.is_allowed(key) is True
            assert limiter.is_allowed(key) is False  # limit hit

            # Advance time beyond the window
            fake_now[0] = 1015.0
            # Old timestamps are now outside [1005, 1015], so slots free up
            assert limiter.is_allowed(key) is True


# ---------------------------------------------------------------------------
# SimpleRateLimiter — shared class-level state
# ---------------------------------------------------------------------------

class TestSimpleRateLimiterSharedState:
    def test_multiple_instances_share_state(self, monkeypatch):
        """Two separate SimpleRateLimiter instances operate on the same backing dict."""
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_REQUESTS", 3)
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 60)

        a = SimpleRateLimiter()
        b = SimpleRateLimiter()

        # Use 2 slots via instance a
        a.is_allowed("shared-state-key")
        a.is_allowed("shared-state-key")

        # Instance b sees both previous requests and can only use 1 more
        assert b.is_allowed("shared-state-key") is True
        assert b.is_allowed("shared-state-key") is False

    def test_requests_dict_is_same_object(self):
        """Both instances' .requests attribute is the exact same dict object."""
        a = SimpleRateLimiter()
        b = SimpleRateLimiter()
        assert a.requests is b.requests

    def test_direct_mutation_visible_across_instances(self):
        """Directly mutating _shared is visible to all instances."""
        a = SimpleRateLimiter()
        b = SimpleRateLimiter()
        SimpleRateLimiter._shared["cross-key"].append(99999.9)
        assert 99999.9 in b.requests["cross-key"]


# ---------------------------------------------------------------------------
# RedisRateLimiter — circuit breaker
# ---------------------------------------------------------------------------

class TestRedisRateLimiterCircuitBreaker:
    def test_circuit_open_returns_false_when_disabled_until_is_none(self):
        """_is_circuit_open is False when disabled_until has never been set."""
        limiter = RedisRateLimiter()
        limiter.disabled_until = None
        assert limiter._is_circuit_open() is False

    def test_circuit_open_returns_true_when_disabled_until_in_future(self):
        """_is_circuit_open is True when disabled_until is a future timestamp."""
        limiter = RedisRateLimiter()
        limiter.disabled_until = time.time() + 60  # 60 seconds from now
        assert limiter._is_circuit_open() is True

    def test_circuit_open_returns_false_when_disabled_until_in_past(self):
        """_is_circuit_open is False once disabled_until has passed."""
        limiter = RedisRateLimiter()
        limiter.disabled_until = time.time() - 1  # 1 second ago
        assert limiter._is_circuit_open() is False

    def test_uses_fallback_when_circuit_is_open(self, monkeypatch):
        """When the circuit is open, is_allowed delegates to the fallback limiter."""
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
        monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_REQUESTS", 10)
        monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 60)

        limiter = RedisRateLimiter()
        limiter.disabled_until = time.time() + 30  # circuit is open

        fallback_calls: list[str] = []

        def mock_fallback_is_allowed(key: str) -> bool:
            fallback_calls.append(key)
            return True

        limiter.fallback.is_allowed = mock_fallback_is_allowed  # type: ignore[method-assign]

        result = limiter.is_allowed("circuit-test-key")
        assert result is True
        assert "circuit-test-key" in fallback_calls, (
            "Fallback should have been called when circuit is open"
        )


# ---------------------------------------------------------------------------
# RedisRateLimiter — key namespacing
# ---------------------------------------------------------------------------

class TestRedisRateLimiterKeyNamespace:
    def test_key_namespace_returns_prefixed_key(self):
        """_key_namespace prefixes the raw key with 'auth_rate_limiter:'."""
        limiter = RedisRateLimiter()
        assert limiter._key_namespace("user123") == "auth_rate_limiter:user123"

    def test_key_namespace_preserves_special_chars(self):
        """_key_namespace does not alter the key beyond prefixing."""
        limiter = RedisRateLimiter()
        raw = "login_ip_192.168.1.1"
        assert limiter._key_namespace(raw) == f"auth_rate_limiter:{raw}"

    def test_key_namespace_empty_string(self):
        """_key_namespace works with an empty string key."""
        limiter = RedisRateLimiter()
        assert limiter._key_namespace("") == "auth_rate_limiter:"


# ---------------------------------------------------------------------------
# Module-level rate_limiter instance
# ---------------------------------------------------------------------------

class TestModuleLevelRateLimiterInstance:
    def test_rate_limiter_is_redis_when_use_redis_and_not_eager(self, monkeypatch):
        """Module-level rate_limiter is RedisRateLimiter when Redis enabled and not eager."""
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
        monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)

        import app.core.rate_limiter as rl_mod

        reload(rl_mod)
        assert isinstance(rl_mod.rate_limiter, RedisRateLimiter)

    def test_rate_limiter_is_simple_when_use_redis_false(self, monkeypatch):
        """Module-level rate_limiter is SimpleRateLimiter when USE_REDIS_RATE_LIMITER=False."""
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", False)

        import app.core.rate_limiter as rl_mod

        reload(rl_mod)
        assert isinstance(rl_mod.rate_limiter, SimpleRateLimiter)

    def test_rate_limiter_is_simple_when_celery_eager(self, monkeypatch):
        """Module-level rate_limiter is SimpleRateLimiter when CELERY_TASK_ALWAYS_EAGER=True."""
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
        monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", True)

        import app.core.rate_limiter as rl_mod

        reload(rl_mod)
        assert isinstance(rl_mod.rate_limiter, SimpleRateLimiter)
