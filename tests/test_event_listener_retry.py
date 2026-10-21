"""Tests for EventListener subscriber retry with backoff (issue #745).

The listener must give a failing subscriber a bounded number of retries with
exponential backoff, isolate the failure from the remaining subscribers, and
record the event exactly once regardless of delivery outcome.
"""

from __future__ import annotations

import logging

import pytest

from app.services.event_listener import EventListener, RetryPolicy

LOGGER_NAME = "app.services.event_listener"


class RecordingSleep:
    """Callable that records backoff delays instead of sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class FlakySubscriber:
    """Subscriber that raises for its first *failures* invocations."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def __call__(self, event: dict) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(f"transient failure {self.calls}")


class AlwaysFailingSubscriber:
    """Subscriber that always raises."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, event: dict) -> None:
        self.calls += 1
        raise RuntimeError("boom")


@pytest.fixture()
def sleep() -> RecordingSleep:
    return RecordingSleep()


@pytest.fixture()
def listener(sleep: RecordingSleep) -> EventListener:
    return EventListener(
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=5.0, jitter=False),
        sleep=sleep,
    )


class TestRetryOnTransientFailure:
    def test_retries_until_the_subscriber_succeeds(self, listener: EventListener, sleep: RecordingSleep) -> None:
        subscriber = FlakySubscriber(failures=2)
        listener.subscribe("outage.created", subscriber)

        listener.process_event({"topic": "outage.created"})

        assert subscriber.calls == 3, "Two failures plus one success"
        assert sleep.delays == [0.1, 0.2], "Exponential backoff between attempts"

    def test_successful_subscriber_is_not_retried(self, listener: EventListener, sleep: RecordingSleep) -> None:
        calls: list[dict] = []

        listener.process_event({"topic": "outage.created"})  # no subscribers yet
        listener.subscribe("outage.created", calls.append)
        listener.process_event({"topic": "outage.created"})

        assert len(calls) == 1
        assert sleep.delays == []

    def test_event_is_stored_exactly_once_while_retrying(self, listener: EventListener) -> None:
        listener.subscribe("outage.created", FlakySubscriber(failures=2))

        listener.process_event({"topic": "outage.created"})

        assert listener.list_events()["total"] == 1
        assert listener.health()["total_events"] == 1


class TestRetryExhaustion:
    def test_gives_up_after_max_attempts(self, listener: EventListener, sleep: RecordingSleep) -> None:
        subscriber = AlwaysFailingSubscriber()
        listener.subscribe("outage.created", subscriber)

        listener.process_event({"topic": "outage.created"})

        assert subscriber.calls == 3
        assert sleep.delays == [0.1, 0.2], "No sleep after the final attempt"

    def test_exhaustion_is_logged_and_not_raised(
        self, listener: EventListener, caplog: pytest.LogCaptureFixture
    ) -> None:
        listener.subscribe("outage.created", AlwaysFailingSubscriber())

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            listener.process_event({"topic": "outage.created"})

        exhausted = [
            record for record in caplog.records if record.getMessage() == "Subscriber failed after all retry attempts"
        ]
        assert len(exhausted) == 1

        record = exhausted[0]
        assert record.topic == "outage.created"
        assert record.error_type == "RuntimeError"
        assert record.attempts == 3
        assert "AlwaysFailingSubscriber" in record.callback

    def test_retry_attempts_are_logged_with_backoff_details(
        self, listener: EventListener, caplog: pytest.LogCaptureFixture
    ) -> None:
        listener.subscribe("outage.created", AlwaysFailingSubscriber())

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            listener.process_event({"topic": "outage.created"})

        retries = [
            record
            for record in caplog.records
            if record.getMessage() == "Subscriber raised an exception, retrying with backoff"
        ]
        assert [record.attempt for record in retries] == [1, 2]
        assert [record.retry_delay_seconds for record in retries] == [0.1, 0.2]
        assert all(record.max_attempts == 3 for record in retries)

    def test_process_event_reports_delivery_counts(
        self, listener: EventListener, caplog: pytest.LogCaptureFixture
    ) -> None:
        listener.subscribe("outage.created", AlwaysFailingSubscriber())
        listener.subscribe("outage.created", lambda event: None)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            listener.process_event({"topic": "outage.created"})

        processed = [record for record in caplog.records if record.getMessage() == "Event processed"]
        assert len(processed) == 1
        assert processed[0].subscribers_delivered == 1
        assert processed[0].subscribers_failed == 1


class TestRetryIsolation:
    def test_one_failing_subscriber_does_not_block_others(self, listener: EventListener) -> None:
        healthy_calls: list[dict] = []
        failing = AlwaysFailingSubscriber()

        listener.subscribe("outage.created", failing)
        listener.subscribe("outage.created", healthy_calls.append)

        listener.process_event({"topic": "outage.created"})

        assert failing.calls == 3
        assert len(healthy_calls) == 1

    def test_wildcard_subscriber_is_retried(self, listener: EventListener) -> None:
        wildcard = AlwaysFailingSubscriber()
        listener.subscribe("*", wildcard)

        listener.process_event({"topic": "payment.completed"})

        assert wildcard.calls == 3


class TestRetryPolicy:
    def test_backoff_is_capped_at_max_delay(self) -> None:
        policy = RetryPolicy(max_attempts=4, base_delay=0.1, max_delay=0.15, jitter=False)

        delays = [policy.delay_for_attempt(attempt) for attempt in (1, 2, 3)]

        assert delays == [0.1, 0.15, 0.15]

    def test_jitter_stays_within_the_capped_delay(self) -> None:
        policy = RetryPolicy(base_delay=0.5, max_delay=1.0, jitter=True)

        delays = [policy.delay_for_attempt(attempt) for attempt in range(1, 6) for _ in range(20)]

        assert all(0.0 <= delay <= 1.0 for delay in delays)
        assert len(set(delays)) > 1, "Jitter should vary the delay"

    def test_single_attempt_disables_retries(self, sleep: RecordingSleep) -> None:
        listener = EventListener(
            retry_policy=RetryPolicy(max_attempts=1, jitter=False),
            sleep=sleep,
        )
        subscriber = AlwaysFailingSubscriber()
        listener.subscribe("outage.created", subscriber)

        listener.process_event({"topic": "outage.created"})

        assert subscriber.calls == 1
        assert sleep.delays == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_attempts": 0},
            {"base_delay": -0.1},
            {"max_delay": -1.0},
        ],
    )
    def test_invalid_configuration_is_rejected(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(**kwargs)

    def test_default_listener_uses_retry_policy(self) -> None:
        listener = EventListener()

        assert isinstance(listener._retry, RetryPolicy)
        assert listener._retry.max_attempts == 3
