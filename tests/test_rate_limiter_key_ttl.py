"""Rate-limit state must not outlive its window (#544).

The issue reports keys accumulating for clients that "scored then went quiet".
Checked against the code, that is true of one store and not the other:

- **Redis**: `RATE_LIMITER_LUA` already re-arms `EXPIRE key window` on every
  scored request, so a key deletes itself once its window lapses. The guard here
  pins that so it cannot be dropped in a refactor.
- **In-process** (`SimpleRateLimiter`, the fallback used when Redis is
  unavailable, disabled, or eager): state lives in a class-level dict and was only
  ever pruned for the key being presented. A key that stopped appearing kept its
  list for the lifetime of the process, which is the leak in the issue title.
"""

import time

import pytest

from app.core.config import settings
from app.core.rate_limiter import (
    RATE_LIMITER_LUA,
    SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD,
    SimpleRateLimiter,
)


@pytest.fixture(autouse=True)
def isolate_shared_state():
    """`_shared` is class-level, so snapshot and restore it around each test."""
    saved = {key: list(hits) for key, hits in SimpleRateLimiter._shared.items()}
    SimpleRateLimiter._shared.clear()
    try:
        yield SimpleRateLimiter._shared
    finally:
        SimpleRateLimiter._shared.clear()
        SimpleRateLimiter._shared.update(saved)


def _age_key(shared, key: str, hits: int = 1, age_seconds: float | None = None) -> None:
    """Seed `key` with hits that are already older than the window."""
    age = settings.AUTH_RATE_LIMIT_WINDOW_SECONDS + 60 if age_seconds is None else age_seconds
    now = time.time()
    shared[key] = [now - age + i for i in range(hits)]


class TestRedisKeysSelfExpire:
    def test_script_arms_expiry_with_the_window_on_every_scored_request(self):
        """Static guard: the expiry is what makes the Redis footprint bounded.

        `fakeredis` here is installed without the `lupa` extra, so `EVAL` is not
        available and an end-to-end TTL assertion cannot run in this environment.
        """
        assert "redis.call('EXPIRE', key, window)" in RATE_LIMITER_LUA
        # Re-armed after the score is recorded, not before, so the TTL always
        # covers the newest hit.
        assert RATE_LIMITER_LUA.index("ZADD") < RATE_LIMITER_LUA.index("EXPIRE")

    def test_expiry_is_not_longer_than_the_window(self):
        """A margin would only extend key life; the window is the useful bound."""
        assert "EXPIRE', key, window +" not in RATE_LIMITER_LUA
        assert "PERSIST" not in RATE_LIMITER_LUA


class TestInProcessKeysAreSwept:
    def test_cull_expired_removes_a_key_that_went_quiet(self, isolate_shared_state):
        limiter = SimpleRateLimiter()
        _age_key(isolate_shared_state, "admin_key", hits=10)

        removed = limiter.cull_expired()

        assert removed == 1
        assert "admin_key" not in isolate_shared_state

    def test_cull_expired_keeps_keys_inside_the_window(self, isolate_shared_state):
        limiter = SimpleRateLimiter()
        isolate_shared_state["active_client"] = [time.time()]

        assert limiter.cull_expired() == 0
        assert "active_client" in isolate_shared_state

    def test_cull_expired_removes_empty_entries(self, isolate_shared_state):
        limiter = SimpleRateLimiter()
        isolate_shared_state["touched_but_never_scored"] = []

        assert limiter.cull_expired() == 1
        assert "touched_but_never_scored" not in isolate_shared_state

    def test_sweep_runs_once_the_map_outgrows_the_threshold(self, isolate_shared_state, monkeypatch):
        monkeypatch.setattr("app.core.rate_limiter.SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD", 4)
        limiter = SimpleRateLimiter()
        _age_key(isolate_shared_state, "admin_key", hits=10)
        _age_key(isolate_shared_state, "scanner_1", hits=3)
        _age_key(isolate_shared_state, "scanner_2", hits=2)
        assert len(isolate_shared_state) == 3

        # Four more distinct clients push the map past the threshold; the sweep
        # fires as part of this request.
        for index in range(4):
            assert limiter.is_allowed(f"fresh_{index}") is True

        assert "admin_key" not in isolate_shared_state
        assert "scanner_1" not in isolate_shared_state
        assert "scanner_2" not in isolate_shared_state

    def test_live_clients_survive_a_sweep_and_stay_rate_limited(self, isolate_shared_state, monkeypatch):
        monkeypatch.setattr("app.core.rate_limiter.SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD", 2)
        limiter = SimpleRateLimiter()
        for _ in range(settings.AUTH_RATE_LIMIT_REQUESTS):
            assert limiter.is_allowed("noisy_client") is True
        _age_key(isolate_shared_state, "ghost")

        # This request crosses the threshold and triggers the sweep.
        assert limiter.is_allowed("another_client") is True

        assert "ghost" not in isolate_shared_state
        assert "noisy_client" in isolate_shared_state
        # The sweep must not hand a rate-limited client a fresh allowance.
        assert limiter.is_allowed("noisy_client") is False

    def test_burst_of_fresh_keys_is_left_alone(self, isolate_shared_state, monkeypatch):
        """The sweep reclaims lapsed keys only — a live burst is not collateral."""
        monkeypatch.setattr("app.core.rate_limiter.SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD", 4)
        limiter = SimpleRateLimiter()
        for index in range(6):
            assert limiter.is_allowed(f"fresh_{index}") is True

        assert len(isolate_shared_state) == 6

    def test_rotating_key_workload_is_reclaimed_once_the_window_lapses(self, isolate_shared_state, monkeypatch):
        """The leak's shape: many keys, each seen once, then never again."""
        monkeypatch.setattr("app.core.rate_limiter.SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD", 8)
        limiter = SimpleRateLimiter()
        total = settings.AUTH_RATE_LIMIT_REQUESTS * 20
        for index in range(total):
            assert limiter.is_allowed(f"scanner_{index}") is True
        assert len(isolate_shared_state) == total

        # One window later the whole working set is reclaimable, without any of
        # those keys being presented again.
        after_window = time.time() + settings.AUTH_RATE_LIMIT_WINDOW_SECONDS + 1

        assert limiter.cull_expired(now=after_window) == total
        assert len(isolate_shared_state) == 0

    def test_default_threshold_is_finite(self):
        assert 0 < SIMPLE_RATE_LIMITER_SWEEP_THRESHOLD < 100_000
