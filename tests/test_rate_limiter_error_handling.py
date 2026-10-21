"""Error-handling tests for the rate limiter (issue #752).

Covers:

* ``check_or_raise`` raising :class:`RateLimitError` with the offending key.
* Retry with exponential backoff on transient Redis failures, on both the
  synchronous and asynchronous code paths.
* Circuit breaking and fallback once Redis stays unavailable.
* Structured log fields emitted on retries and fallbacks.

The tests use small stand-ins for the Redis clients rather than ``fakeredis``:
the limiter depends on the ``EVAL`` command, which fakeredis does not
implement, so a fake would silently exercise the fallback path.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from time import time

import pytest
from redis.exceptions import RedisError

import app.core.rate_limiter as rl_mod
from app.core.config import settings
from app.core.rate_limiter import REDIS_MAX_ATTEMPTS, RedisRateLimiter, SimpleRateLimiter


class RecordingSleep:
    """Record backoff delays instead of sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class _WindowStore:
    """Sliding-window rule shared by the sync and async Redis stand-ins.

    Args:
        fail_with: When set, ``eval`` raises this exception instead of applying
            the rule.
        fail_times: When *fail_with* is set, the number of calls that raise
            before the rule runs (``None`` = always fail).
    """

    def __init__(self, fail_with: Exception | None = None, fail_times: int | None = None) -> None:
        self.calls = 0
        self.fail_with = fail_with
        self.fail_times = fail_times
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def _register_call(self) -> None:
        self.calls += 1
        if self.fail_with is not None and (self.fail_times is None or self.calls <= self.fail_times):
            raise self.fail_with

    def _apply(self, key: str, now: int, window: int, limit: int, member: str) -> int:
        window_start = now - window
        entries = [timestamp for timestamp in self._buckets[key] if timestamp > window_start]
        if len(entries) >= limit:
            self._buckets[key] = entries
            return 0

        entries.append(now)
        self._buckets[key] = entries
        return 1


class FakeRedisClient(_WindowStore):
    """Synchronous ``EVAL`` stand-in."""

    def eval(self, script, numkeys, key, now, window, limit, member) -> int:
        self._register_call()
        return self._apply(key, now, window, limit, member)


class FakeAsyncRedisClient(_WindowStore):
    """Asynchronous ``EVAL`` stand-in."""

    async def eval(self, script, numkeys, key, now, window, limit, member) -> int:
        self._register_call()
        return self._apply(key, now, window, limit, member)


@pytest.fixture(autouse=True)
def reset_shared_limiter_state():
    """Keep the class-level memory state isolated between tests.

    The dict is cleared in place so the module-level ``_shared_fallback``
    instance (which holds a reference to it) is reset too.
    """
    rl_mod.SimpleRateLimiter._shared.clear()
    yield
    rl_mod.SimpleRateLimiter._shared.clear()


@pytest.fixture()
def sleep() -> RecordingSleep:
    return RecordingSleep()


@pytest.fixture()
def redis_limiter(monkeypatch, sleep: RecordingSleep) -> RedisRateLimiter:
    monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", True)
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    limiter = rl_mod.RedisRateLimiter(sleep=sleep)
    limiter.client = FakeRedisClient()
    limiter.async_client = FakeAsyncRedisClient()
    return limiter


@pytest.fixture()
def captured_logs():
    """Capture records on the limiter logger itself.

    ``caplog`` relies on propagation to the root logger, which earlier tests in
    the session disturb (``dictConfig`` in ``app.main``, and the alembic
    round-trip test disables existing loggers).  Attaching a handler to the
    limiter logger and re-enabling it keeps these assertions independent of the
    global logging configuration.
    """
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    log = rl_mod.logger
    handler = _ListHandler()
    previous_level, previous_propagate, previous_disabled = (
        log.level,
        log.propagate,
        log.disabled,
    )
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    log.propagate = True
    log.disabled = False
    try:
        yield records
    finally:
        log.removeHandler(handler)
        log.setLevel(previous_level)
        log.propagate = previous_propagate
        log.disabled = previous_disabled


@pytest.fixture()
def async_sleeps(monkeypatch) -> list[float]:
    """Record ``asyncio.sleep`` delays used by the async retry path."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(rl_mod.asyncio, "sleep", fake_sleep)
    return delays


def use_client(
    limiter: RedisRateLimiter,
    sync_client: FakeRedisClient | None = None,
    async_client: FakeAsyncRedisClient | None = None,
) -> None:
    """Install stand-in clients on *limiter*."""
    if sync_client is not None:
        limiter.client = sync_client
    if async_client is not None:
        limiter.async_client = async_client


class TestRateLimitError:
    def test_carries_key_and_message(self) -> None:
        error = rl_mod.RateLimitError("login_ip_203.0.113.7")

        assert error.key == "login_ip_203.0.113.7"
        assert "login_ip_203.0.113.7" in str(error)

    def test_is_an_exception(self) -> None:
        assert issubclass(rl_mod.RateLimitError, Exception)


class TestSimpleRateLimiterCheckOrRaise:
    def test_allows_requests_up_to_limit(self) -> None:
        limiter = rl_mod.SimpleRateLimiter()

        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            limiter.check_or_raise("simple-key")  # must not raise

    def test_raises_after_limit(self) -> None:
        limiter = rl_mod.SimpleRateLimiter()
        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            limiter.check_or_raise("simple-key")

        with pytest.raises(rl_mod.RateLimitError) as exc_info:
            limiter.check_or_raise("simple-key")

        assert exc_info.value.key == "simple-key"

    def test_keys_are_independent(self) -> None:
        limiter = rl_mod.SimpleRateLimiter()
        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            limiter.check_or_raise("exhausted-key")

        limiter.check_or_raise("fresh-key")  # must not raise

    def test_exceeded_limit_is_logged(self, captured_logs: list[logging.LogRecord]) -> None:
        limiter = rl_mod.SimpleRateLimiter()
        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            limiter.is_allowed("logged-key")

        with pytest.raises(rl_mod.RateLimitError):
            limiter.check_or_raise("logged-key")

        records = [r for r in captured_logs if r.getMessage() == "Rate limit exceeded"]
        assert len(records) == 1
        assert records[0].rate_limiter_key == "logged-key"
        assert records[0].backend == "memory"
        assert records[0].circuit_open is False


class TestRedisRateLimiterCheckOrRaise:
    def test_allows_requests_up_to_limit(self, redis_limiter: RedisRateLimiter) -> None:
        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            redis_limiter.check_or_raise("redis-key")  # must not raise

    def test_raises_after_limit(self, redis_limiter: RedisRateLimiter) -> None:
        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            redis_limiter.check_or_raise("redis-key")

        with pytest.raises(rl_mod.RateLimitError) as exc_info:
            redis_limiter.check_or_raise("redis-key")

        assert exc_info.value.key == "redis-key"

    def test_uses_redis_when_available(self, redis_limiter: RedisRateLimiter) -> None:
        client = redis_limiter.client

        assert redis_limiter.is_allowed("redis-backed-key") is True
        assert client.calls == 1
        assert redis_limiter._is_circuit_open() is False


class TestRedisRetryWithBackoff:
    def test_transient_failure_is_retried_and_succeeds(
        self,
        redis_limiter: RedisRateLimiter,
        sleep: RecordingSleep,
        captured_logs: list[logging.LogRecord],
    ) -> None:
        client = FakeRedisClient(fail_with=RedisError("blip"), fail_times=1)
        use_client(redis_limiter, sync_client=client)

        allowed = redis_limiter.is_allowed("retry-key")

        assert allowed is True
        assert client.calls == 2, "One failure plus one successful retry"
        assert sleep.delays == [rl_mod.REDIS_BASE_DELAY_SECONDS]
        assert redis_limiter._is_circuit_open() is False

        retries = [
            record for record in captured_logs if record.getMessage().startswith("Redis rate limiter check failed")
        ]
        assert len(retries) == 1
        assert retries[0].rate_limiter_key == "retry-key"
        assert retries[0].backend == "redis"
        assert retries[0].error_type == "RedisError"
        assert retries[0].attempt == 1
        assert retries[0].max_attempts == REDIS_MAX_ATTEMPTS
        assert retries[0].retry_delay_seconds == rl_mod.REDIS_BASE_DELAY_SECONDS

    def test_backoff_grows_then_falls_back(self, redis_limiter: RedisRateLimiter, sleep: RecordingSleep) -> None:
        use_client(redis_limiter, sync_client=FakeRedisClient(fail_with=RedisError("down")))

        allowed = redis_limiter.is_allowed("fallback-key")

        assert allowed is True, "In-memory fallback must still allow the request"
        assert sleep.delays == [
            rl_mod.REDIS_BASE_DELAY_SECONDS,
            rl_mod.REDIS_BASE_DELAY_SECONDS * 2,
        ]
        assert redis_limiter._is_circuit_open() is True

    def test_fallback_exhaustion_is_logged(
        self, redis_limiter: RedisRateLimiter, captured_logs: list[logging.LogRecord]
    ) -> None:
        use_client(redis_limiter, sync_client=FakeRedisClient(fail_with=RedisError("down")))

        redis_limiter.is_allowed("fallback-log-key")

        fallbacks = [
            record
            for record in captured_logs
            if record.getMessage().startswith("Redis rate limiter unavailable after retries")
        ]
        assert len(fallbacks) == 1
        record = fallbacks[0]
        assert record.rate_limiter_key == "fallback-log-key"
        assert record.backend == "in-memory limiter"
        assert record.error_type == "RedisError"
        assert record.attempt == REDIS_MAX_ATTEMPTS

    def test_non_retryable_error_falls_back_immediately(
        self,
        redis_limiter: RedisRateLimiter,
        sleep: RecordingSleep,
        captured_logs: list[logging.LogRecord],
    ) -> None:
        client = FakeRedisClient(fail_with=RuntimeError("loop bug"))
        use_client(redis_limiter, sync_client=client)

        allowed = redis_limiter.is_allowed("unexpected-error-key")

        assert allowed is True
        assert client.calls == 1, "Unexpected errors must not be retried"
        assert sleep.delays == []
        assert redis_limiter._is_circuit_open() is True

        records = [
            record for record in captured_logs if record.getMessage().startswith("Unexpected rate limiter error")
        ]
        assert len(records) == 1
        assert records[0].error_type == "RuntimeError"
        assert records[0].rate_limiter_key == "unexpected-error-key"


class TestAsyncRedisPath:
    def test_allows_requests_up_to_limit(self, redis_limiter: RedisRateLimiter) -> None:
        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            assert asyncio.run(redis_limiter.is_allowed_async("async-key")) is True

        assert asyncio.run(redis_limiter.is_allowed_async("async-key")) is False

    def test_transient_failure_is_retried(
        self,
        redis_limiter: RedisRateLimiter,
        async_sleeps: list[float],
    ) -> None:
        client = FakeAsyncRedisClient(fail_with=RedisError("blip"), fail_times=1)
        use_client(redis_limiter, async_client=client)

        allowed = asyncio.run(redis_limiter.is_allowed_async("async-retry-key"))

        assert allowed is True
        assert client.calls == 2
        assert async_sleeps == [rl_mod.REDIS_BASE_DELAY_SECONDS]

    def test_persistent_failure_falls_back(
        self,
        redis_limiter: RedisRateLimiter,
        async_sleeps: list[float],
    ) -> None:
        client = FakeAsyncRedisClient(fail_with=RedisError("down"))
        use_client(redis_limiter, async_client=client)

        allowed = asyncio.run(redis_limiter.is_allowed_async("async-fallback-key"))

        assert allowed is True
        assert client.calls == REDIS_MAX_ATTEMPTS
        assert len(async_sleeps) == REDIS_MAX_ATTEMPTS - 1
        assert redis_limiter._is_circuit_open() is True


class TestCircuitBreaker:
    def test_open_circuit_skips_redis(self, redis_limiter: RedisRateLimiter) -> None:
        client = FakeRedisClient()
        use_client(redis_limiter, sync_client=client)
        redis_limiter.disabled_until = time() + 30

        assert redis_limiter.is_allowed("circuit-key") is True
        assert client.calls == 0

    def test_circuit_closes_after_reset_window(self, redis_limiter: RedisRateLimiter) -> None:
        redis_limiter.disabled_until = time() - 1

        assert redis_limiter._is_circuit_open() is False
        assert redis_limiter.is_allowed("reset-key") is True

    def test_fallback_limit_is_still_enforced(self, redis_limiter: RedisRateLimiter) -> None:
        redis_limiter._trip_circuit()

        results = [redis_limiter.is_allowed("degraded-key") for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS)]
        assert all(results)
        assert redis_limiter.is_allowed("degraded-key") is False

    def test_check_or_raise_uses_fallback_after_redis_failure(self, redis_limiter: RedisRateLimiter) -> None:
        use_client(redis_limiter, sync_client=FakeRedisClient(fail_with=RedisError("down")))

        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            redis_limiter.check_or_raise("degraded-raise-key")

        with pytest.raises(rl_mod.RateLimitError) as exc_info:
            redis_limiter.check_or_raise("degraded-raise-key")

        assert exc_info.value.key == "degraded-raise-key"


class TestRedisDisabled:
    def test_uses_memory_when_redis_disabled(
        self, redis_limiter: RedisRateLimiter, sleep: RecordingSleep, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", False)
        client = FakeRedisClient()
        use_client(redis_limiter, sync_client=client)

        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            assert redis_limiter.is_allowed("disabled-key") is True

        assert redis_limiter.is_allowed("disabled-key") is False
        assert client.calls == 0
        assert sleep.delays == []

    def test_check_or_raise_raises_when_redis_disabled(self, redis_limiter: RedisRateLimiter, monkeypatch) -> None:
        monkeypatch.setattr(settings, "USE_REDIS_RATE_LIMITER", False)

        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            redis_limiter.check_or_raise("disabled-raise-key")

        with pytest.raises(rl_mod.RateLimitError):
            redis_limiter.check_or_raise("disabled-raise-key")


class TestPersistentLedgerPath:
    """The ``db`` path must report limits and degrade without raising."""

    def test_rejects_when_ledger_rejects(self, redis_limiter: RedisRateLimiter, monkeypatch) -> None:
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda *a, **k: False)

        assert redis_limiter.is_allowed("ledger-key", db=object()) is False

    def test_check_or_raise_uses_ledger(self, redis_limiter: RedisRateLimiter, monkeypatch, captured_logs) -> None:
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda *a, **k: False)

        with pytest.raises(rl_mod.RateLimitError):
            redis_limiter.check_or_raise("ledger-raise-key", db=object())

        records = [r for r in captured_logs if r.getMessage() == "Rate limit exceeded"]
        assert records[0].backend == "ledger"

    def test_redis_failure_falls_back_to_ledger(self, redis_limiter: RedisRateLimiter, monkeypatch) -> None:
        monkeypatch.setattr(rl_mod, "record_rate_limit_attempt", lambda *a, **k: True)
        use_client(redis_limiter, async_client=FakeAsyncRedisClient(fail_with=RedisError("down")))

        assert redis_limiter.is_allowed("ledger-fallback-key", db=object()) is True
        assert redis_limiter._is_circuit_open() is True


class TestBackoffDelay:
    def test_doubles_per_attempt(self) -> None:
        assert rl_mod._backoff_delay(1) == rl_mod.REDIS_BASE_DELAY_SECONDS
        assert rl_mod._backoff_delay(2) == rl_mod.REDIS_BASE_DELAY_SECONDS * 2
        assert rl_mod._backoff_delay(3) == rl_mod.REDIS_BASE_DELAY_SECONDS * 4

    def test_is_capped(self) -> None:
        assert rl_mod._backoff_delay(50) == rl_mod.REDIS_MAX_DELAY_SECONDS


def test_simple_limiter_type_shares_class_state() -> None:
    """The module-level fallback and new instances agree on the same window."""
    a = SimpleRateLimiter()
    b = SimpleRateLimiter()
    a.requests["shared-key"].append(1.0)

    assert 1.0 in b.requests["shared-key"]
