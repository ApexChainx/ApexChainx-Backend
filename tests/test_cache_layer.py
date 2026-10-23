import json

import fakeredis
import pytest
from redis.exceptions import ConnectionError

from app.services.cache_layer import CacheLayer


def test_cache_layer_round_trips_json_and_uses_namespace():
    redis = fakeredis.FakeRedis()
    cache = CacheLayer(redis, namespace="sla", ttl_seconds=30)

    assert cache.set("summary/2026-10", {"uptime": 99.9})
    assert cache.get("summary/2026-10") == {"uptime": 99.9}
    assert redis.ttl("sla:summary/2026-10") > 0


def test_cache_layer_rejects_unsafe_keys_and_ttls():
    cache = CacheLayer(fakeredis.FakeRedis())

    with pytest.raises(ValueError):
        cache.get("../secrets")
    with pytest.raises(ValueError):
        cache.set("ok", {}, ttl_seconds=0)


def test_cache_layer_evicts_corrupt_values():
    redis = fakeredis.FakeRedis()
    cache = CacheLayer(redis, namespace="test")
    redis.set("test:bad", "not-json")

    assert cache.get("bad") is None
    assert redis.get("test:bad") is None


def test_cache_layer_retries_redis_errors_then_recovers(monkeypatch):
    redis = fakeredis.FakeRedis()
    cache = CacheLayer(redis, max_attempts=3, backoff_seconds=0)
    calls = 0
    original = redis.setex

    def flaky_setex(*args):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("temporary")
        return original(*args)

    monkeypatch.setattr(redis, "setex", flaky_setex)
    assert cache.set("key", {"value": 1})
    assert calls == 3
    assert json.loads(redis.get("app:key")) == {"value": 1}


def test_cache_layer_fails_open_after_redis_retries():
    class BrokenRedis:
        def get(self, _key):
            raise ConnectionError("offline")

    cache = CacheLayer(BrokenRedis(), max_attempts=2, backoff_seconds=0)
    assert cache.get("key") is None

