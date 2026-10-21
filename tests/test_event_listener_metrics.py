"""Tests for the Prometheus series of the event listener (issue #738).

The listener is on the path every domain event takes, so the questions an
operator asks about it have to be answerable from the metrics endpoint.
These tests pin the series names and the tags they carry.
"""

from app.services.event_listener_metrics import (
    EVENT_LISTENER_DISPATCH_TOTAL,
    EVENT_LISTENER_ERROR_TOTAL,
    EVENT_LISTENER_RESOLUTION_DURATION,
    EVENT_LISTENER_RESOLUTION_TOTAL,
    EVENT_LISTENER_SUBSCRIBER_SKIPPED_TOTAL,
    EVENT_LISTENER_SUBSCRIBERS,
    EventListenerMetrics,
)
from app.services.metrics import metrics


def _counters() -> dict[str, float]:
    return metrics.get_metrics_summary()["counters"]


def _gauges() -> dict[str, float]:
    return metrics.get_metrics_summary()["gauges"]


def _histograms() -> dict[str, dict]:
    return metrics.get_metrics_summary()["histograms"]


def test_a_resolution_is_counted_and_tagged_with_the_event():
    listener = EventListenerMetrics()

    listener.resolution_started("sla.violation")
    listener.resolution_completed("sla.violation", subscriber_count=3, duration_seconds=0.01)

    counters = _counters()
    assert any(key.startswith(EVENT_LISTENER_RESOLUTION_TOTAL) for key in counters)
    assert any("event=sla.violation" in key for key in counters)


def test_the_subscriber_count_is_published_as_a_gauge():
    listener = EventListenerMetrics()

    listener.resolution_completed("sla.violation", subscriber_count=7, duration_seconds=0.01)

    gauges = _gauges()
    assert any(key.startswith(EVENT_LISTENER_SUBSCRIBERS) for key in gauges)
    assert any("event=sla.violation" in key for key in gauges)


def test_the_resolution_duration_is_published_as_a_histogram():
    listener = EventListenerMetrics()

    listener.resolution_completed("sla.violation", subscriber_count=1, duration_seconds=0.25)

    histograms = _histograms()
    assert any(key.startswith(EVENT_LISTENER_RESOLUTION_DURATION) for key in histograms)


def test_a_dispatch_is_counted():
    listener = EventListenerMetrics()

    listener.dispatch_completed("sla.violation", delivery_count=4)

    counters = _counters()
    assert any(key.startswith(EVENT_LISTENER_DISPATCH_TOTAL) for key in counters)


def test_a_skipped_subscriber_is_counted_with_its_reason():
    listener = EventListenerMetrics()

    listener.subscriber_skipped("sla.violation", reason="rate_limited")

    counters = _counters()
    assert any(key.startswith(EVENT_LISTENER_SUBSCRIBER_SKIPPED_TOTAL) for key in counters)
    assert any("reason=rate_limited" in key for key in counters)


def test_an_error_is_counted_with_the_stage_it_happened_in():
    listener = EventListenerMetrics()

    listener.error("sla.violation", stage="resolve")

    counters = _counters()
    assert any(key.startswith(EVENT_LISTENER_ERROR_TOTAL) for key in counters)
    assert any("stage=resolve" in key for key in counters)


def test_a_disabled_listener_publishes_nothing():
    listener = EventListenerMetrics(enabled=False)
    before = set(_counters())

    listener.resolution_started("sla.violation")
    listener.dispatch_completed("sla.violation", delivery_count=1)
    listener.error("sla.violation", stage="resolve")
    listener.subscriber_skipped("sla.violation", reason="rate_limited")

    assert set(_counters()) == before
