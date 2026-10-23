"""Unit tests for WalletCache (#29).

Pins two properties:
1. A degraded Redis is always a miss — no exception ever reaches the caller.
2. The round-trip (set → get) returns the original dict; corrupt entries are
   treated as misses.
"""

import json

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from app.services.wallet_cache import WalletCache

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

WALLET = {"address": "GABC", "balance": "100.00", "asset_code": "XLM"}


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
    """Every command raises OSError (e.g. broken pipe)."""

    def get(self, *_, **__):
        raise OSError("broken pipe")

    def setex(self, *_, **__):
        raise OSError("broken pipe")

    def delete(self, *_, **__):
        raise OSError("broken pipe")


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


class TestWalletCacheRoundTrip:
    def test_miss_returns_none(self):
        assert WalletCache(FakeRedis()).get("GABC") is None

    def test_set_then_get_returns_dict(self):
        cache = WalletCache(FakeRedis())
        cache.set("GABC", WALLET)
        assert cache.get("GABC") == WALLET

    def test_set_stores_under_wallet_prefix(self):
        redis = FakeRedis()
        cache = WalletCache(redis)
        cache.set("GABC", WALLET)
        assert "wallet:GABC" in redis.store

    def test_set_respects_ttl(self):
        redis = FakeRedis()
        cache = WalletCache(redis, ttl=120)
        cache.set("GABC", WALLET)
        assert redis.ttls["wallet:GABC"] == 120

    def test_invalidate_removes_entry(self):
        cache = WalletCache(FakeRedis())
        cache.set("GABC", WALLET)
        cache.invalidate("GABC")
        assert cache.get("GABC") is None

    def test_invalidate_on_missing_key_is_a_noop(self):
        # Should not raise even if the key never existed.
        WalletCache(FakeRedis()).invalidate("GNONE")

    def test_get_returns_none_for_empty_bytes(self):
        redis = FakeRedis()
        redis.store["wallet:GABC"] = b""
        assert WalletCache(redis).get("GABC") is None


# ---------------------------------------------------------------------------
# Corrupt data
# ---------------------------------------------------------------------------


class TestWalletCacheCorruptData:
    def test_corrupt_json_on_get_is_a_miss(self):
        redis = FakeRedis()
        redis.store["wallet:GABC"] = b"not-json"
        assert WalletCache(redis).get("GABC") is None

    def test_corrupt_json_logs_warning(self, caplog):
        import logging

        redis = FakeRedis()
        redis.store["wallet:GABC"] = b"not-json"
        with caplog.at_level(logging.WARNING, logger="app.services.wallet_cache"):
            WalletCache(redis).get("GABC")
        assert any("Corrupt JSON" in r.message for r in caplog.records)

    def test_invalid_unicode_on_get_is_a_miss(self):
        redis = FakeRedis()
        redis.store["wallet:GABC"] = b"\xff\xfe"
        assert WalletCache(redis).get("GABC") is None


# ---------------------------------------------------------------------------
# Redis failure → cache miss, never a raised exception
# ---------------------------------------------------------------------------


class TestWalletCacheRedisFailure:
    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_get_never_raises(self, redis_cls):
        result = WalletCache(redis_cls()).get("GABC")
        assert result is None

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_set_never_raises(self, redis_cls):
        WalletCache(redis_cls()).set("GABC", WALLET)  # must not raise

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_invalidate_never_raises(self, redis_cls):
        WalletCache(redis_cls()).invalidate("GABC")  # must not raise

    def test_get_failure_logs_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="app.services.wallet_cache"):
            WalletCache(BrokenRedis()).get("GABC")
        assert any("read failed" in r.message for r in caplog.records)

    def test_set_failure_logs_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="app.services.wallet_cache"):
            WalletCache(BrokenRedis()).set("GABC", WALLET)
        assert any("write failed" in r.message for r in caplog.records)

    def test_invalidate_failure_logs_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="app.services.wallet_cache"):
            WalletCache(BrokenRedis()).invalidate("GABC")
        assert any("invalidate failed" in r.message for r in caplog.records)

    def test_consecutive_redis_failures_do_not_accumulate_exceptions(self):
        cache = WalletCache(BrokenRedis())
        for _ in range(10):
            assert cache.get("GABC") is None


# ---------------------------------------------------------------------------
# Key isolation
# ---------------------------------------------------------------------------


class TestWalletCacheKeyIsolation:
    def test_different_addresses_do_not_collide(self):
        cache = WalletCache(FakeRedis())
        cache.set("GABC", {"v": 1})
        cache.set("GXYZ", {"v": 2})
        assert cache.get("GABC") == {"v": 1}
        assert cache.get("GXYZ") == {"v": 2}

    def test_invalidate_only_removes_targeted_address(self):
        cache = WalletCache(FakeRedis())
        cache.set("GABC", {"v": 1})
        cache.set("GXYZ", {"v": 2})
        cache.invalidate("GABC")
        assert cache.get("GABC") is None
        assert cache.get("GXYZ") == {"v": 2}
