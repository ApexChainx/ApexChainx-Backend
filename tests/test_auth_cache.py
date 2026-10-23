"""Unit tests for AuthCache (#660) and UserCache (#676).

Both classes share the same error-handling contract:
- Redis failures → cache miss (None / empty), never a raised exception.
- Corrupt JSON → cache miss, never a raised exception.
- Successful round-trips store/return the original data faithfully.
"""

import json
import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.auth_cache import AuthCache
from app.services.user_cache import UserCache

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

USER = {"id": "u1", "email": "alice@example.com", "role": "admin"}
SESSIONS = [
    {"session_id": "s1", "created_at": "2026-10-01T00:00:00"},
    {"session_id": "s2", "created_at": "2026-10-02T00:00:00"},
]


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


# ===========================================================================
# AuthCache
# ===========================================================================


class TestAuthCacheRoundTrip:
    def test_miss_returns_none(self):
        assert AuthCache(FakeRedis()).get_user("alice@example.com") is None

    def test_set_then_get_returns_dict(self):
        cache = AuthCache(FakeRedis())
        cache.set_user("alice@example.com", USER)
        assert cache.get_user("alice@example.com") == USER

    def test_key_uses_auth_prefix(self):
        redis = FakeRedis()
        cache = AuthCache(redis)
        cache.set_user("alice@example.com", USER)
        assert "auth:user:alice@example.com" in redis.store

    def test_set_respects_ttl(self):
        redis = FakeRedis()
        cache = AuthCache(redis, ttl=90)
        cache.set_user("alice@example.com", USER)
        assert redis.ttls["auth:user:alice@example.com"] == 90

    def test_invalidate_removes_entry(self):
        cache = AuthCache(FakeRedis())
        cache.set_user("alice@example.com", USER)
        cache.invalidate_user("alice@example.com")
        assert cache.get_user("alice@example.com") is None

    def test_invalidate_on_missing_key_is_a_noop(self):
        AuthCache(FakeRedis()).invalidate_user("nobody@example.com")

    def test_different_emails_do_not_collide(self):
        cache = AuthCache(FakeRedis())
        cache.set_user("a@x.com", {"id": "1"})
        cache.set_user("b@x.com", {"id": "2"})
        assert cache.get_user("a@x.com") == {"id": "1"}
        assert cache.get_user("b@x.com") == {"id": "2"}


class TestAuthCacheCorruptData:
    def test_corrupt_json_is_a_miss(self):
        redis = FakeRedis()
        redis.store["auth:user:alice@example.com"] = b"not-json"
        assert AuthCache(redis).get_user("alice@example.com") is None

    def test_corrupt_json_logs_warning(self, caplog):
        redis = FakeRedis()
        redis.store["auth:user:alice@example.com"] = b"not-json"
        with caplog.at_level(logging.WARNING, logger="app.services.auth_cache"):
            AuthCache(redis).get_user("alice@example.com")
        assert any("Corrupt JSON" in r.message for r in caplog.records)

    def test_invalid_unicode_is_a_miss(self):
        redis = FakeRedis()
        redis.store["auth:user:alice@example.com"] = b"\xff\xfe"
        assert AuthCache(redis).get_user("alice@example.com") is None


class TestAuthCacheRedisFailure:
    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_get_user_never_raises(self, redis_cls):
        assert AuthCache(redis_cls()).get_user("alice@example.com") is None

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_set_user_never_raises(self, redis_cls):
        AuthCache(redis_cls()).set_user("alice@example.com", USER)

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_invalidate_user_never_raises(self, redis_cls):
        AuthCache(redis_cls()).invalidate_user("alice@example.com")

    def test_get_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.auth_cache"):
            AuthCache(BrokenRedis()).get_user("alice@example.com")
        assert any("read failed" in r.message for r in caplog.records)

    def test_set_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.auth_cache"):
            AuthCache(BrokenRedis()).set_user("alice@example.com", USER)
        assert any("write failed" in r.message for r in caplog.records)

    def test_invalidate_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.auth_cache"):
            AuthCache(BrokenRedis()).invalidate_user("alice@example.com")
        assert any("invalidate failed" in r.message for r in caplog.records)

    def test_repeated_failures_never_accumulate_exceptions(self):
        cache = AuthCache(BrokenRedis())
        for _ in range(10):
            assert cache.get_user("alice@example.com") is None


# ===========================================================================
# UserCache
# ===========================================================================


class TestUserCacheRoundTrip:
    def test_miss_returns_none(self):
        assert UserCache(FakeRedis()).get_sessions("alice@example.com") is None

    def test_set_then_get_returns_list(self):
        cache = UserCache(FakeRedis())
        cache.set_sessions("alice@example.com", SESSIONS)
        assert cache.get_sessions("alice@example.com") == SESSIONS

    def test_key_uses_user_sessions_prefix(self):
        redis = FakeRedis()
        cache = UserCache(redis)
        cache.set_sessions("alice@example.com", SESSIONS)
        assert "user:sessions:alice@example.com" in redis.store

    def test_set_respects_ttl(self):
        redis = FakeRedis()
        cache = UserCache(redis, ttl=45)
        cache.set_sessions("alice@example.com", SESSIONS)
        assert redis.ttls["user:sessions:alice@example.com"] == 45

    def test_invalidate_removes_entry(self):
        cache = UserCache(FakeRedis())
        cache.set_sessions("alice@example.com", SESSIONS)
        cache.invalidate("alice@example.com")
        assert cache.get_sessions("alice@example.com") is None

    def test_invalidate_on_missing_key_is_a_noop(self):
        UserCache(FakeRedis()).invalidate("nobody@example.com")

    def test_different_emails_do_not_collide(self):
        cache = UserCache(FakeRedis())
        cache.set_sessions("a@x.com", [{"id": "s1"}])
        cache.set_sessions("b@x.com", [{"id": "s2"}])
        assert cache.get_sessions("a@x.com") == [{"id": "s1"}]
        assert cache.get_sessions("b@x.com") == [{"id": "s2"}]

    def test_empty_sessions_list_round_trips(self):
        cache = UserCache(FakeRedis())
        cache.set_sessions("alice@example.com", [])
        # An empty list stored as "[]" is falsy in bytes form only if it decoded
        # to an empty string; JSON "[]" decodes to a list so it must be returned.
        result = cache.get_sessions("alice@example.com")
        assert result == []

    def test_datetime_values_are_serialised_as_strings(self):
        """set_sessions uses default=str so datetime objects must not crash."""
        from datetime import datetime, timezone

        sessions_with_dt = [{"session_id": "s1", "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc)}]
        cache = UserCache(FakeRedis())
        cache.set_sessions("alice@example.com", sessions_with_dt)
        result = cache.get_sessions("alice@example.com")
        # The datetime is serialised to a string and returned as-is.
        assert isinstance(result[0]["created_at"], str)


class TestUserCacheCorruptData:
    def test_corrupt_json_is_a_miss(self):
        redis = FakeRedis()
        redis.store["user:sessions:alice@example.com"] = b"not-json"
        assert UserCache(redis).get_sessions("alice@example.com") is None

    def test_corrupt_json_logs_warning(self, caplog):
        redis = FakeRedis()
        redis.store["user:sessions:alice@example.com"] = b"not-json"
        with caplog.at_level(logging.WARNING, logger="app.services.user_cache"):
            UserCache(redis).get_sessions("alice@example.com")
        assert any("Corrupt JSON" in r.message for r in caplog.records)

    def test_invalid_unicode_is_a_miss(self):
        redis = FakeRedis()
        redis.store["user:sessions:alice@example.com"] = b"\xff\xfe"
        assert UserCache(redis).get_sessions("alice@example.com") is None


class TestUserCacheRedisFailure:
    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_get_sessions_never_raises(self, redis_cls):
        assert UserCache(redis_cls()).get_sessions("alice@example.com") is None

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_set_sessions_never_raises(self, redis_cls):
        UserCache(redis_cls()).set_sessions("alice@example.com", SESSIONS)

    @pytest.mark.parametrize("redis_cls", [BrokenRedis, OSErrorRedis])
    def test_invalidate_never_raises(self, redis_cls):
        UserCache(redis_cls()).invalidate("alice@example.com")

    def test_get_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.user_cache"):
            UserCache(BrokenRedis()).get_sessions("alice@example.com")
        assert any("read failed" in r.message for r in caplog.records)

    def test_set_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.user_cache"):
            UserCache(BrokenRedis()).set_sessions("alice@example.com", SESSIONS)
        assert any("write failed" in r.message for r in caplog.records)

    def test_invalidate_failure_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.user_cache"):
            UserCache(BrokenRedis()).invalidate("alice@example.com")
        assert any("invalidate failed" in r.message for r in caplog.records)
