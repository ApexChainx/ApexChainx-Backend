"""Tests for multi-worker rate limiter consistency — Issue #238.

Validates that USE_REDIS_RATE_LIMITER defaults to True so that
Redis-backed rate limiting is used across multiple Gunicorn workers.
"""

import pytest

from app.core.config import settings
from app.core.rate_limiter import RedisRateLimiter, SimpleRateLimiter, rate_limiter


class TestRateLimiterDefault:
    def test_use_redis_rate_limiter_defaults_to_true(self):
        """USE_REDIS_RATE_LIMITER must be True so Redis is the default store."""
        assert settings.USE_REDIS_RATE_LIMITER is True

    def test_module_level_rate_limiter_is_redis_when_enabled(self, monkeypatch):
        """When USE_REDIS_RATE_LIMITER=True and not eager, rate_limiter is RedisRateLimiter."""
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
        monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)

        from importlib import reload

        import app.core.rate_limiter as rl_mod

        reload(rl_mod)
        assert isinstance(rl_mod.rate_limiter, RedisRateLimiter)

    def test_module_level_rate_limiter_is_simple_when_disabled(self, monkeypatch):
        """When USE_REDIS_RATE_LIMITER=False, rate_limiter is SimpleRateLimiter."""
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", False)

        from importlib import reload

        import app.core.rate_limiter as rl_mod

        reload(rl_mod)
        assert isinstance(rl_mod.rate_limiter, SimpleRateLimiter)

    def test_module_level_rate_limiter_is_simple_when_eager(self, monkeypatch):
        """When CELERY_TASK_ALWAYS_EAGER=True, rate_limiter is SimpleRateLimiter even if Redis enabled."""
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
        monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", True)

        from importlib import reload

        import app.core.rate_limiter as rl_mod

        reload(rl_mod)
        assert isinstance(rl_mod.rate_limiter, SimpleRateLimiter)


class TestSimpleRateLimiterIsolation:
    """Demonstrate that SimpleRateLimiter state is per-class, not per-process."""

    def test_shared_class_level_state(self):
        """Multiple SimpleRateLimiter instances share the same class-level dict."""
        a = SimpleRateLimiter()
        b = SimpleRateLimiter()
        a.requests["test_key"].append(1.0)
        assert 1.0 in b.requests["test_key"]
