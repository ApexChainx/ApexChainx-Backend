"""Unit tests for token_revocation.

Pins three properties:
1. Happy-path: revoke stores a key; is_revoked returns True for revoked hashes
   and False for unknown hashes.
2. revoke is fire-and-forget: a Redis failure logs a warning and returns
   without raising — the logout path is never blocked.
3. is_revoked fails open: a Redis failure returns False (not revoked) and
   logs a warning — authenticated users are never locked out by a Redis outage.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

import app.services.token_revocation as revocation_module
from app.services.token_revocation import is_revoked, revoke

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOKEN_HASH = "abc123def456"
TTL = 3600


def _make_fake_redis(store=None):
    """Return a MagicMock that acts as a minimal Redis client."""
    redis = MagicMock()
    _store: dict = store if store is not None else {}

    def _setex(key, ttl, value):
        _store[key] = value

    def _exists(key):
        return 1 if key in _store else 0

    redis.setex.side_effect = _setex
    redis.exists.side_effect = _exists
    return redis, _store


def _make_broken_redis(exc=None):
    """Return a MagicMock whose setex/exists raise RedisConnectionError."""
    redis = MagicMock()
    error = exc or RedisConnectionError("redis is down")
    redis.setex.side_effect = error
    redis.exists.side_effect = error
    return redis


# ---------------------------------------------------------------------------
# Fixture: reset the module-level singleton between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_revocation_redis():
    """Ensure the module-level Redis singleton is cleared before each test."""
    revocation_module._revocation_redis = None
    yield
    revocation_module._revocation_redis = None


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


class TestTokenRevocationHappyPath:
    def test_revoke_stores_key_in_redis(self):
        fake_redis, store = _make_fake_redis()
        with patch.object(revocation_module, "_get_redis", return_value=fake_redis):
            revoke(TOKEN_HASH, TTL)
        assert any(TOKEN_HASH in key for key in store)

    def test_is_revoked_returns_true_for_revoked_hash(self):
        fake_redis, _ = _make_fake_redis()
        with patch.object(revocation_module, "_get_redis", return_value=fake_redis):
            revoke(TOKEN_HASH, TTL)
            assert is_revoked(TOKEN_HASH) is True

    def test_is_revoked_returns_false_for_unknown_hash(self):
        fake_redis, _ = _make_fake_redis()
        with patch.object(revocation_module, "_get_redis", return_value=fake_redis):
            assert is_revoked("unknown_hash") is False

    def test_revoke_passes_ttl_to_setex(self):
        fake_redis = MagicMock()
        with patch.object(revocation_module, "_get_redis", return_value=fake_redis):
            revoke(TOKEN_HASH, TTL)
        call_kwargs = fake_redis.setex.call_args
        # setex(key, ttl, value) — ttl is the second positional argument
        assert call_kwargs[0][1] == TTL

    def test_revoke_key_includes_configured_prefix(self):
        from app.core.config import settings

        fake_redis, store = _make_fake_redis()
        with patch.object(revocation_module, "_get_redis", return_value=fake_redis):
            revoke(TOKEN_HASH, TTL)
        expected_key = f"{settings.AUTH_REVOCATION_KEY_PREFIX}:{TOKEN_HASH}"
        assert expected_key in store

    def test_revoke_stores_sentinel_value_one(self):
        fake_redis, store = _make_fake_redis()
        from app.core.config import settings

        with patch.object(revocation_module, "_get_redis", return_value=fake_redis):
            revoke(TOKEN_HASH, TTL)
        key = f"{settings.AUTH_REVOCATION_KEY_PREFIX}:{TOKEN_HASH}"
        assert store[key] == "1"


# ---------------------------------------------------------------------------
# revoke — Redis failure handling
# ---------------------------------------------------------------------------


class TestRevokeRedisFailure:
    @pytest.mark.parametrize(
        "exc",
        [
            RedisConnectionError("down"),
            RedisError("generic"),
            OSError("broken pipe"),
        ],
    )
    def test_revoke_never_raises(self, exc):
        broken = _make_broken_redis(exc)
        with patch.object(revocation_module, "_get_redis", return_value=broken):
            revoke(TOKEN_HASH, TTL)  # must not raise

    def test_revoke_logs_warning_on_failure(self, caplog):
        broken = _make_broken_redis()
        with patch.object(revocation_module, "_get_redis", return_value=broken):
            with caplog.at_level(logging.WARNING, logger="app.services.token_revocation"):
                revoke(TOKEN_HASH, TTL)
        assert any("revocation write failed" in r.message.lower() for r in caplog.records)

    def test_revoke_warning_includes_token_hash(self, caplog):
        broken = _make_broken_redis()
        with patch.object(revocation_module, "_get_redis", return_value=broken):
            with caplog.at_level(logging.WARNING, logger="app.services.token_revocation"):
                revoke(TOKEN_HASH, TTL)
        assert any(TOKEN_HASH in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# is_revoked — Redis failure handling (fail-open)
# ---------------------------------------------------------------------------


class TestIsRevokedRedisFailure:
    @pytest.mark.parametrize(
        "exc",
        [
            RedisConnectionError("down"),
            RedisError("generic"),
            OSError("broken pipe"),
        ],
    )
    def test_is_revoked_never_raises(self, exc):
        broken = _make_broken_redis(exc)
        with patch.object(revocation_module, "_get_redis", return_value=broken):
            result = is_revoked(TOKEN_HASH)
        assert result is False  # fail-open

    def test_is_revoked_returns_false_on_failure(self):
        """Fail-open: a Redis outage must not block authenticated users."""
        broken = _make_broken_redis()
        with patch.object(revocation_module, "_get_redis", return_value=broken):
            assert is_revoked(TOKEN_HASH) is False

    def test_is_revoked_logs_warning_on_failure(self, caplog):
        broken = _make_broken_redis()
        with patch.object(revocation_module, "_get_redis", return_value=broken):
            with caplog.at_level(logging.WARNING, logger="app.services.token_revocation"):
                is_revoked(TOKEN_HASH)
        assert any("revocation read failed" in r.message.lower() for r in caplog.records)

    def test_is_revoked_warning_includes_token_hash(self, caplog):
        broken = _make_broken_redis()
        with patch.object(revocation_module, "_get_redis", return_value=broken):
            with caplog.at_level(logging.WARNING, logger="app.services.token_revocation"):
                is_revoked(TOKEN_HASH)
        assert any(TOKEN_HASH in r.message for r in caplog.records)

    def test_repeated_failures_do_not_accumulate_exceptions(self):
        broken = _make_broken_redis()
        with patch.object(revocation_module, "_get_redis", return_value=broken):
            for _ in range(10):
                assert is_revoked(TOKEN_HASH) is False


# ---------------------------------------------------------------------------
# Singleton initialisation
# ---------------------------------------------------------------------------


class TestGetRedis:
    def test_get_redis_returns_a_redis_instance(self):
        """_get_redis() must create a client on first call."""
        with patch("app.services.token_revocation.Redis") as mock_redis_cls:
            instance = MagicMock()
            mock_redis_cls.from_url.return_value = instance
            result = revocation_module._get_redis()
        assert result is instance

    def test_get_redis_reuses_existing_client(self):
        """Second call must not create a new client."""
        fake_client = MagicMock()
        revocation_module._revocation_redis = fake_client
        result = revocation_module._get_redis()
        assert result is fake_client
