import asyncio

import pytest
from fakeredis.aioredis import FakeRedis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.rate_limiter import RedisRateLimiter


@pytest.fixture(autouse=True)
def reset_rate_limiter_settings(monkeypatch):
    monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    yield


@pytest.fixture
def fake_redis():
    client = FakeRedis()
    try:
        yield client
    finally:
        asyncio.run(client.flushall())
        asyncio.run(client.close())


def test_redis_rate_limiter_allows_up_to_limit_and_rejects_11th(fake_redis):
    limiter = RedisRateLimiter()
    limiter.client = fake_redis

    allowed = [limiter.is_allowed("login_ip_1.2.3.4") for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS)]
    assert all(allowed)
    assert limiter.is_allowed("login_ip_1.2.3.4") is False


def test_redis_rate_limiter_shared_state_across_instances(fake_redis):
    limiter_a = RedisRateLimiter()
    limiter_b = RedisRateLimiter()
    limiter_a.client = fake_redis
    limiter_b.client = fake_redis

    for _ in range(5):
        assert limiter_a.is_allowed("login_ip_5.6.7.8") is True

    for _ in range(5):
        assert limiter_b.is_allowed("login_ip_5.6.7.8") is True

    assert limiter_a.is_allowed("login_ip_5.6.7.8") is False


def test_redis_rate_limiter_falls_back_gracefully_when_redis_unreachable(monkeypatch, fake_redis):
    limiter = RedisRateLimiter()
    limiter.client = fake_redis

    async def fail_eval(*args, **kwargs):
        raise RedisError("network failure")

    monkeypatch.setattr(limiter.client, "eval", fail_eval)

    # First call should fall back and be allowed
    assert limiter.is_allowed("refresh_ip_9.9.9.9") is True

    # Circuit is now open, fallback should preserve state and reject after 10 attempts
    for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS - 1):
        assert limiter.is_allowed("refresh_ip_9.9.9.9") is True

    assert limiter.is_allowed("refresh_ip_9.9.9.9") is False
