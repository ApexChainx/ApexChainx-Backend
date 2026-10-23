"""Unit tests for SLACache (#25).

Pins three properties:
1. Happy-path round-trip: set → get returns the stored dict, warm_up
   pre-populates keys readable by get, get_or_compute stores and returns.
2. Corrupt-data resilience: invalid JSON is always a miss, never an error.
3. Redis-failure resilience: every operation swallows (RedisError, OSError)
   and treats the result as a miss — no exception ever escapes SLACache.
"""

import json
import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.sla_cache import SLACache

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

RESULT = {"device_id": "dev-1", "period": "2026-09", "availability_percentage": 99.95}


class FakeRedis:
    """Minimal Redis stand-in backed by a plain dict."""

    def __init__(self):
        self.store: dict = {}
        self.ttls: dict = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value.encode() if isinstance(value, str) else value
        self.ttls[key] = ttl

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0


class BrokenRedis:
    """Every command raises RedisConnectionError."""

    def __init__(self):
        self.calls = 0

    def _fail(self, *_, **__):
        self.calls += 1
        raise RedisConnectionError("redis is down")

    get = setex = delete = _fail


class OSErrorRedis:
    """Every command raises OSError."""

    def get(self, *_, **__):
        raise OSError("broken pipe")

    def setex(self, *_, **__):
        raise OSError("broken pipe")

    def delete(self, *_, **__):
        raise OSError("broken pipe")


class FailOnSetRedis(FakeRedis):
    """Reads succeed; writes raise RedisConnectionError."""

    def setex(self, *_, **__):
        raise RedisConnectionError("write failed")


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


class TestSLACacheRoundTrip:
    def test_miss_returns_none(self):
        assert SLACache(FakeRedis()).get("dev-1", "2026-09") is None

    def test_set_then_get_returns_dict(self):
        cache = SLACache(FakeRedis())
        cache.set("dev-1", "2026-09", RESULT)
        assert cache.get("dev-1", "2026-09") == RESULT

    def test_key_uses_sla_prefix(self):
        redis = FakeRedis()
        cache = SLACache(redis)
        cache.set("dev-1", "2026-09", RESULT)
        assert "sla:dev-1:2026-09" in redis.store

    def test_set_respects_ttl(self):
        redis = FakeRedis()
        cache = SLACache(redis, ttl=300)
        cache.set("dev-1", "2026-09", RESULT)
        assert redis.ttls["sla:dev-1:2026-09"] == 300

    def test_invalidate_removes_entry(self):
        cache = SLACache(FakeRedis())
        cache.set("dev-1", "2026-09", RESULT)
        cache.invalidate("dev-1", "2026-09")
        assert cache.get("dev-1", "2026-09") is None

    def test_invalidate_on_missing_key_is_a_noop(self):
        SLACache(FakeRedis()).invalidate("dev-x", "2026-09")

    def test_get_returns_none_for_empty_value(self):
        redis = FakeRedis()
        redis.store["sla:dev-1:2026-09"] = b""
        assert SLACache(redis).get("dev-1", "2026-09") is None


# ---------------------------------------------------------------------------
# get_or_compute
# ---------------------------------------------------------------------------


class TestGetOrCompute:
    def test_cache_miss_calls_compute_fn(self):
        calls = []

        def compute():
            calls.append(1)
            return RESULT

        result = SLACache(FakeRedis()).get_or_compute("dev-1", "2026-09", compute)
        assert result == RESULT
        assert len(calls) == 1

    def test_cache_hit_skips_compute_fn(self):
        cache = SLACache(FakeRedis())
        cache.set("dev-1", "2026-09", RESULT)
        calls = []

        result = cache.get_or_compute("dev-1", "2026-09", lambda: calls.append(1) or {})
        assert result == RESULT
        assert calls == []

    def test_compute_result_is_stored_for_next_call(self):
        cache = SLACache(FakeRedis())
        cache.get_or_compute("dev-1", "2026-09", lambda: RESULT)
        # Second call must be a cache hit.
        calls = []
        cache.get_or_compute("dev-1", "2026-09", lambda: calls.append(1) or {})
        assert calls == []

    def test_write_failure_still_returns_computed_value(self):
        """If the cache write fails the computed value is still returned."""
        cache = SLACache(FailOnSetRedis())
        result = cache.get_or_compute("dev-1", "2026-09", lambda: RESULT)
        assert result == RESULT


# ---------------------------------------------------------------------------
# warm_up
# ---------------------------------------------------------------------------


class TestWarmUp:
    def test_warm_up_writes_through_read_path(self):
        cache = SLACache(FakeRedis())
        cache.warm_up([("dev-1", "2026-09", RESULT)])
        assert cache.get("dev-1", "2026-09") == RESULT

    def test_warm_up_returns_count_of_entries_written(self):
        cache = SLACache(FakeRedis())
        n = cache.warm_up([
            ("dev-1", "2026-09", RESULT),
            ("dev-2", "2026-09", RESULT),
        ])
        assert n == 2

    def test_warm_up_returns_zero_for_empty_input(self):
        assert SLACache(FakeRedis()).warm_up([]) == 0

    def test_warm_up_skips_failed_entries_without_aborting(self):
        """A single bad entry must not cancel the remaining warm-up."""
        redis = FakeRedis()
        call_count = [0]
        original_setex = redis.setex

        def flaky_setex(key, ttl, value):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RedisConnectionError("flaky")
            original_setex(key, ttl, value)

        redis.setex = flaky_setex
        cache = SLACache(redis)
        n = cache.warm_up([
            ("bad", "2026-09", RESULT),
            ("good", "2026-09", RESULT),
        ])
        # Only the second entry succeeded.
        assert n == 1
        assert cache.get("good", "2026-09") == RESULT

    def test_warm_up_uses_same_ttl_as_set(self):
        redis = FakeRedis()
        cache = SLACache(redis, ttl=180)
        cache.warm_up([("dev-1", "2026-09", RESULT)])
        assert redis.ttls["sla:dev-1:2026-09"] == 180


# ---------------------------------------------------------------------------
# Corrupt data
# ---------------------------------------------------------------------------


class TestSLACacheCorruptData:
    def test_corrupt_json_is_a_miss(self):
        redis = FakeRedis()
        redis.store["sla:dev-1:2026-09"] = b"{{bad"
        assert SLACache(redis).get("dev-1", "2026-09") is None

    def test_corrupt_json_logs_warning(self, caplog):
        redis = FakeRedis()
        redis.store["sla:dev-1:2026-09"] = b"{{bad"
        with caplog.at_level(logging.WARNING, logger="app.services.sla_cache"):
            SLACache(redis).get("dev-1", "2026-09")
        assert any("Corrupt JSON" in r.message for r in caplog.records)

    def test_invalid_unicode_is_a_miss(self):
        redis = FakeRedis()
        redis.store["sla:dev-1:2026-09"] = b"\xff\xfe"
        assert SLACache(redis).get("dev-1", "2026-09") is None


# ---------------------------------------------------------------------------
# Redis failure → cache miss, never a raised exception
# ---------------------------------------------------------------------------


class TestSLACacheRedisFailure:
    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_get_never_raises(self, redis_cls):
        assert SLACache(redis_cls()).get("dev-1", "2026-09") is None

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_set_never_raises(self, redis_cls):
        SLACache(redis_cls()).set("dev-1", "2026-09", RESULT)

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_invalidate_never_raises(self, redis_cls):
        SLACache(redis_cls()).invalidate("dev-1", "2026-09")

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_get_or_compute_never_raises(self, redis_cls):
        result = SLACache(redis_cls()).get_or_compute("dev-1", "2026-09", lambda: RESULT)
        assert result == RESULT

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_warm_up_never_raises(self, redis_cls):
        n = SLACache(redis_cls()).warm_up([("dev-1", "2026-09", RESULT)])
        # warm_up catches failures per-entry; all entries failed → 0 written
        assert n == 0

    def test_get_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.sla_cache"):
            SLACache(BrokenRedis()).get("dev-1", "2026-09")
        assert any("read failed" in r.message for r in caplog.records)

    def test_set_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.sla_cache"):
            SLACache(BrokenRedis()).set("dev-1", "2026-09", RESULT)
        assert any("write failed" in r.message for r in caplog.records)

    def test_invalidate_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.sla_cache"):
            SLACache(BrokenRedis()).invalidate("dev-1", "2026-09")
        assert any("invalidate failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Key isolation
# ---------------------------------------------------------------------------


class TestSLACacheKeyIsolation:
    def test_different_devices_do_not_collide(self):
        cache = SLACache(FakeRedis())
        cache.set("dev-1", "2026-09", {"v": 1})
        cache.set("dev-2", "2026-09", {"v": 2})
        assert cache.get("dev-1", "2026-09") == {"v": 1}
        assert cache.get("dev-2", "2026-09") == {"v": 2}

    def test_different_periods_do_not_collide(self):
        cache = SLACache(FakeRedis())
        cache.set("dev-1", "2026-08", {"v": 1})
        cache.set("dev-1", "2026-09", {"v": 2})
        assert cache.get("dev-1", "2026-08") == {"v": 1}
        assert cache.get("dev-1", "2026-09") == {"v": 2}

    def test_invalidate_only_removes_targeted_entry(self):
        cache = SLACache(FakeRedis())
        cache.set("dev-1", "2026-09", {"v": 1})
        cache.set("dev-2", "2026-09", {"v": 2})
        cache.invalidate("dev-1", "2026-09")
        assert cache.get("dev-1", "2026-09") is None
        assert cache.get("dev-2", "2026-09") == {"v": 2}
