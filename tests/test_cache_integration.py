"""Integration tests for the full CacheLayer tier (fakeredis).

Uses ``fakeredis.FakeRedis(decode_responses=True)`` so keys and values are
returned as strings — matching the behaviour of the production Redis client
used by all cache classes (they write string keys and expect string/bytes
back from `get`).  TTL, key namespacing, cross-class isolation, and
serialisation are all exercised here against a real Redis implementation
without a network connection.

Marked ``pytest.mark.redis`` so the conftest skip-guard fires gracefully if
fakeredis has an import problem, but fakeredis is a dev dependency and
carries no network requirement.

Scenarios covered
-----------------
1. Key namespace isolation — no two cache classes ever share a key prefix.
2. TTL is set correctly on every write path.
3. Invalidation across classes is independent.
4. Entries are absent after TTL expiry (forced via PEXPIRE).
5. Cross-class coherence on a shared keyspace.
6. Concurrent (interleaved) writes to different classes.
7. token_revocation end-to-end round-trip.
8. SLACache.get_or_compute and warm_up integration.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import fakeredis
import pytest

from app.services.auth_cache import AuthCache
from app.services.sla_cache import SLACache
from app.services.user_cache import UserCache
from app.services.wallet_cache import WalletCache
import app.services.token_revocation as revocation_module

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.redis  # infra marker — skipped gracefully if needed


@pytest.fixture()
def redis():
    """A single fakeredis instance shared by all cache objects in a test.

    ``decode_responses=True`` means keys() and get() return strings, which
    matches how every cache class uses Redis (string keys, JSON-encoded values
    already decoded by json.loads before they reach the caller).
    """
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def wallet_cache(redis):
    return WalletCache(redis, ttl=60)


@pytest.fixture()
def sla_cache(redis):
    return SLACache(redis, ttl=120)


@pytest.fixture()
def auth_cache(redis):
    return AuthCache(redis, ttl=90)


@pytest.fixture()
def user_cache(redis):
    return UserCache(redis, ttl=30)


# Sample payloads
WALLET = {"address": "GABC", "balance": "50.00", "asset_code": "XLM"}
SLA_RESULT = {"device_id": "dev-1", "period": "2026-09", "availability_percentage": 99.9}
USER_DATA = {"id": "u1", "email": "alice@example.com", "role": "admin"}
SESSIONS = [{"session_id": "s1"}, {"session_id": "s2"}]


# ---------------------------------------------------------------------------
# 1. Key namespace isolation
# ---------------------------------------------------------------------------


class TestKeyNamespaceIsolation:
    """No two cache classes share a key prefix."""

    def test_wallet_and_sla_keys_do_not_collide(self, redis, wallet_cache, sla_cache):
        wallet_cache.set("GABC", WALLET)
        sla_cache.set("GABC", "2026-09", SLA_RESULT)

        wallet_keys = [k for k in redis.keys() if k.startswith("wallet:")]
        sla_keys = [k for k in redis.keys() if k.startswith("sla:")]
        assert len(wallet_keys) == 1
        assert len(sla_keys) == 1
        assert not (set(wallet_keys) & set(sla_keys))

    def test_auth_and_user_keys_do_not_collide(self, redis, auth_cache, user_cache):
        auth_cache.set_user("alice@example.com", USER_DATA)
        user_cache.set_sessions("alice@example.com", SESSIONS)

        auth_keys = [k for k in redis.keys() if k.startswith("auth:")]
        user_keys = [k for k in redis.keys() if k.startswith("user:")]
        assert len(auth_keys) == 1
        assert len(user_keys) == 1
        assert not (set(auth_keys) & set(user_keys))

    def test_all_four_classes_use_distinct_prefixes(
        self, redis, wallet_cache, sla_cache, auth_cache, user_cache
    ):
        wallet_cache.set("GABC", WALLET)
        sla_cache.set("dev-1", "2026-09", SLA_RESULT)
        auth_cache.set_user("alice@example.com", USER_DATA)
        user_cache.set_sessions("alice@example.com", SESSIONS)

        prefixes = {k.split(":")[0] for k in redis.keys()}
        assert prefixes == {"wallet", "sla", "auth", "user"}


# ---------------------------------------------------------------------------
# 2. TTL is written correctly
# ---------------------------------------------------------------------------


class TestTTLPersistence:
    def test_wallet_cache_ttl(self, redis, wallet_cache):
        wallet_cache.set("GABC", WALLET)
        ttl = redis.ttl("wallet:GABC")
        assert 0 < ttl <= 60

    def test_sla_cache_ttl(self, redis, sla_cache):
        sla_cache.set("dev-1", "2026-09", SLA_RESULT)
        ttl = redis.ttl("sla:dev-1:2026-09")
        assert 0 < ttl <= 120

    def test_auth_cache_ttl(self, redis, auth_cache):
        auth_cache.set_user("alice@example.com", USER_DATA)
        ttl = redis.ttl("auth:user:alice@example.com")
        assert 0 < ttl <= 90

    def test_user_cache_ttl(self, redis, user_cache):
        user_cache.set_sessions("alice@example.com", SESSIONS)
        ttl = redis.ttl("user:sessions:alice@example.com")
        assert 0 < ttl <= 30

    def test_sla_warm_up_ttl_matches_set_ttl(self, redis, sla_cache):
        sla_cache.warm_up([("dev-1", "2026-09", SLA_RESULT)])
        ttl = redis.ttl("sla:dev-1:2026-09")
        assert 0 < ttl <= sla_cache._ttl


# ---------------------------------------------------------------------------
# 3. Invalidation is independent across classes
# ---------------------------------------------------------------------------


class TestCrossClassInvalidation:
    def test_invalidating_wallet_does_not_affect_sla(self, wallet_cache, sla_cache):
        wallet_cache.set("GABC", WALLET)
        sla_cache.set("dev-1", "2026-09", SLA_RESULT)

        wallet_cache.invalidate("GABC")

        assert wallet_cache.get("GABC") is None
        assert sla_cache.get("dev-1", "2026-09") == SLA_RESULT

    def test_invalidating_auth_does_not_affect_user_sessions(self, auth_cache, user_cache):
        auth_cache.set_user("alice@example.com", USER_DATA)
        user_cache.set_sessions("alice@example.com", SESSIONS)

        auth_cache.invalidate_user("alice@example.com")

        assert auth_cache.get_user("alice@example.com") is None
        assert user_cache.get_sessions("alice@example.com") == SESSIONS

    def test_invalidating_sla_does_not_affect_wallet(self, wallet_cache, sla_cache):
        wallet_cache.set("GABC", WALLET)
        sla_cache.set("dev-1", "2026-09", SLA_RESULT)

        sla_cache.invalidate("dev-1", "2026-09")

        assert sla_cache.get("dev-1", "2026-09") is None
        assert wallet_cache.get("GABC") == WALLET


# ---------------------------------------------------------------------------
# 4. Entries are absent after TTL expiry
# ---------------------------------------------------------------------------


class TestTTLExpiry:
    def test_wallet_entry_expires(self, redis):
        cache = WalletCache(redis, ttl=60)
        cache.set("GABC", WALLET)
        # Expire immediately via PEXPIRE (1 ms), then sleep past expiry.
        redis.pexpire("wallet:GABC", 1)
        time.sleep(0.05)
        assert cache.get("GABC") is None

    def test_sla_entry_expires(self, redis):
        cache = SLACache(redis, ttl=120)
        cache.set("dev-1", "2026-09", SLA_RESULT)
        redis.pexpire("sla:dev-1:2026-09", 1)
        time.sleep(0.05)
        assert cache.get("dev-1", "2026-09") is None

    def test_auth_entry_expires(self, redis):
        cache = AuthCache(redis, ttl=90)
        cache.set_user("alice@example.com", USER_DATA)
        redis.pexpire("auth:user:alice@example.com", 1)
        time.sleep(0.05)
        assert cache.get_user("alice@example.com") is None

    def test_user_entry_expires(self, redis):
        cache = UserCache(redis, ttl=30)
        cache.set_sessions("alice@example.com", SESSIONS)
        redis.pexpire("user:sessions:alice@example.com", 1)
        time.sleep(0.05)
        assert cache.get_sessions("alice@example.com") is None


# ---------------------------------------------------------------------------
# 5. Cross-class coherence on a shared keyspace
# ---------------------------------------------------------------------------


class TestSharedKeyspaceCoherence:
    def test_writes_from_all_classes_coexist(
        self, wallet_cache, sla_cache, auth_cache, user_cache
    ):
        wallet_cache.set("GABC", WALLET)
        sla_cache.set("dev-1", "2026-09", SLA_RESULT)
        auth_cache.set_user("alice@example.com", USER_DATA)
        user_cache.set_sessions("alice@example.com", SESSIONS)

        assert wallet_cache.get("GABC") == WALLET
        assert sla_cache.get("dev-1", "2026-09") == SLA_RESULT
        assert auth_cache.get_user("alice@example.com") == USER_DATA
        assert user_cache.get_sessions("alice@example.com") == SESSIONS

    def test_overwriting_one_class_does_not_affect_others(
        self, wallet_cache, sla_cache, auth_cache, user_cache
    ):
        wallet_cache.set("GABC", WALLET)
        sla_cache.set("dev-1", "2026-09", SLA_RESULT)
        auth_cache.set_user("alice@example.com", USER_DATA)
        user_cache.set_sessions("alice@example.com", SESSIONS)

        new_wallet = {**WALLET, "balance": "999.00"}
        wallet_cache.set("GABC", new_wallet)

        assert wallet_cache.get("GABC") == new_wallet
        assert sla_cache.get("dev-1", "2026-09") == SLA_RESULT
        assert auth_cache.get_user("alice@example.com") == USER_DATA
        assert user_cache.get_sessions("alice@example.com") == SESSIONS


# ---------------------------------------------------------------------------
# 6. Concurrent (interleaved) writes to different classes
# ---------------------------------------------------------------------------


class TestConcurrentWrites:
    def test_interleaved_writes_are_all_readable(
        self, wallet_cache, sla_cache, auth_cache, user_cache
    ):
        for i in range(5):
            wallet_cache.set(f"G{i:04d}", {"index": i})
            sla_cache.set(f"dev-{i}", "2026-09", {"index": i})
            auth_cache.set_user(f"user{i}@x.com", {"id": str(i)})
            user_cache.set_sessions(f"user{i}@x.com", [{"sid": i}])

        for i in range(5):
            assert wallet_cache.get(f"G{i:04d}") == {"index": i}
            assert sla_cache.get(f"dev-{i}", "2026-09") == {"index": i}
            assert auth_cache.get_user(f"user{i}@x.com") == {"id": str(i)}
            assert user_cache.get_sessions(f"user{i}@x.com") == [{"sid": i}]


# ---------------------------------------------------------------------------
# 7. token_revocation end-to-end through fakeredis
# ---------------------------------------------------------------------------


class TestTokenRevocationIntegration:
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        revocation_module._revocation_redis = None
        yield
        revocation_module._revocation_redis = None

    @pytest.fixture()
    def rev_redis(self):
        return fakeredis.FakeRedis(decode_responses=True)

    def test_revoke_then_is_revoked_returns_true(self, rev_redis):
        with patch.object(revocation_module, "_get_redis", return_value=rev_redis):
            revocation_module.revoke("hash_abc", ttl_seconds=300)
            assert revocation_module.is_revoked("hash_abc") is True

    def test_unknown_hash_is_not_revoked(self, rev_redis):
        with patch.object(revocation_module, "_get_redis", return_value=rev_redis):
            assert revocation_module.is_revoked("unknown_hash") is False

    def test_revoked_key_uses_configured_prefix(self, rev_redis):
        from app.core.config import settings

        with patch.object(revocation_module, "_get_redis", return_value=rev_redis):
            revocation_module.revoke("hash_abc", ttl_seconds=300)

        expected = f"{settings.AUTH_REVOCATION_KEY_PREFIX}:hash_abc"
        assert rev_redis.exists(expected) == 1

    def test_revoked_key_has_correct_ttl(self, rev_redis):
        from app.core.config import settings

        with patch.object(revocation_module, "_get_redis", return_value=rev_redis):
            revocation_module.revoke("hash_abc", ttl_seconds=300)

        key = f"{settings.AUTH_REVOCATION_KEY_PREFIX}:hash_abc"
        assert 0 < rev_redis.ttl(key) <= 300

    def test_revoke_does_not_bleed_into_other_namespaces(self, rev_redis):
        """Revocation keys must stay within their own prefix."""
        with patch.object(revocation_module, "_get_redis", return_value=rev_redis):
            revocation_module.revoke("hash_abc", ttl_seconds=300)

        all_keys = rev_redis.keys("*")
        non_revocation = [k for k in all_keys if not k.startswith("revoked_token")]
        assert non_revocation == []

    def test_multiple_revocations_are_independent(self, rev_redis):
        with patch.object(revocation_module, "_get_redis", return_value=rev_redis):
            revocation_module.revoke("hash_1", ttl_seconds=300)
            revocation_module.revoke("hash_2", ttl_seconds=300)
            assert revocation_module.is_revoked("hash_1") is True
            assert revocation_module.is_revoked("hash_2") is True
            assert revocation_module.is_revoked("hash_3") is False


# ---------------------------------------------------------------------------
# 8. SLACache.get_or_compute and warm_up integration
# ---------------------------------------------------------------------------


class TestSLACacheGetOrComputeIntegration:
    def test_compute_fn_called_once_then_cached(self, sla_cache):
        calls = []

        def compute():
            calls.append(1)
            return SLA_RESULT

        result1 = sla_cache.get_or_compute("dev-1", "2026-09", compute)
        result2 = sla_cache.get_or_compute("dev-1", "2026-09", compute)

        assert result1 == SLA_RESULT
        assert result2 == SLA_RESULT
        assert len(calls) == 1

    def test_invalidate_forces_recompute(self, sla_cache):
        calls = []

        def compute():
            calls.append(1)
            return SLA_RESULT

        sla_cache.get_or_compute("dev-1", "2026-09", compute)
        sla_cache.invalidate("dev-1", "2026-09")
        sla_cache.get_or_compute("dev-1", "2026-09", compute)

        assert len(calls) == 2

    def test_warm_up_prevents_compute_on_first_read(self, sla_cache):
        sla_cache.warm_up([("dev-1", "2026-09", SLA_RESULT)])
        calls = []
        result = sla_cache.get_or_compute("dev-1", "2026-09", lambda: calls.append(1) or {})
        assert result == SLA_RESULT
        assert calls == []

    def test_warm_up_multiple_entries_all_readable(self, sla_cache):
        entries = [(f"dev-{i}", "2026-09", {"index": i}) for i in range(5)]
        n = sla_cache.warm_up(entries)
        assert n == 5
        for i in range(5):
            assert sla_cache.get(f"dev-{i}", "2026-09") == {"index": i}
