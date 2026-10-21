"""Tests for the event fan-out budget (issue #741).

The budget is what stops one subscriber from amplifying the whole fan-out:
the number of events a subscriber receives is not under its control (one
event per affected device), so the limit has to be enforced by the backend.
"""

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.event_listener_rate_limit import EventRateLimiter


class ExplodingRedis:
    """A Redis client whose every command fails, like a Redis that is down."""

    def __getattr__(self, name):
        def _raise(*args, **kwargs):
            raise RedisConnectionError("redis is down")

        return _raise


def test_subscriber_may_receive_up_to_its_budget():
    limiter = EventRateLimiter(None, limit=3, window_seconds=60)

    assert [limiter.allow("sub-1") for _ in range(3)] == [True, True, True]
    assert limiter.allow("sub-1") is False


def test_budget_is_tracked_per_subscriber():
    limiter = EventRateLimiter(None, limit=1, window_seconds=60)

    assert limiter.allow("sub-1") is True
    # A different subscriber has its own budget.
    assert limiter.allow("sub-2") is True
    assert limiter.allow("sub-1") is False


def test_redis_failure_falls_back_to_the_in_process_limiter():
    limiter = EventRateLimiter(ExplodingRedis(), limit=2, window_seconds=60)

    assert limiter.allow("sub-1") is True
    assert limiter.allow("sub-1") is True
    # The fallback still enforces the budget.
    assert limiter.allow("sub-1") is False


def test_retry_after_reports_the_remaining_window():
    limiter = EventRateLimiter(None, limit=1, window_seconds=60)

    assert limiter.allow("sub-1") is True
    assert limiter.allow("sub-1") is False

    retry_after = limiter.retry_after_seconds("sub-1")
    assert 0 < retry_after <= 60


def test_cull_expired_drops_subscribers_that_stopped_receiving():
    limiter = EventRateLimiter(None, limit=5, window_seconds=60)
    limiter.allow("sub-1")

    assert limiter.cull_expired(now=0.0) == 0
    # Far in the future every recorded hit is outside the window.
    assert limiter.cull_expired(now=10_000.0) == 1


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_budget_is_rejected_by_the_configuration_guard(limit):
    # The configuration guard rejects these at startup; the unit test pins
    # the message so a typo in the guard is caught here too.
    from app.core.config import Settings, validate_critical_settings

    settings = Settings(
        _env_file=None,
        ENVIRONMENT="local",
        EVENT_LISTENER_RATE_LIMIT_PER_SUBSCRIBER=limit,
    )

    with pytest.raises(ValueError, match="EVENT_LISTENER_RATE_LIMIT_PER_SUBSCRIBER must be > 0"):
        validate_critical_settings(settings)
