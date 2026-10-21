"""Tests for the structured log lines of the event listener (issue #737).

The listener makes a small number of decisions, and each of them has to be
greppable: a log line whose meaning lives in free text cannot be queried.
These tests pin that every line carries a stable ``event_name``, the domain
event it is about, and the fields that belong to it.
"""

import json
import logging

from app.services.event_listener_logging import (
    DISPATCH_COMPLETED,
    RESOLVE_COMPLETED,
    RESOLVE_FAILED,
    SUBSCRIBER_SKIPPED,
    EventListenerLogger,
)


def _payloads(caplog) -> list[dict]:
    """Return the structured payloads of the captured log records."""
    payloads = []
    for record in caplog.records:
        try:
            payload = json.loads(record.getMessage())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def test_a_resolve_line_carries_the_event_and_the_subscriber_count(caplog):
    caplog.set_level(logging.INFO)
    listener = EventListenerLogger("test.event_listener")

    listener.resolve_completed("sla.violation", subscriber_count=3, duration_ms=12.5)

    payloads = _payloads(caplog)
    assert payloads
    payload = payloads[-1]
    assert payload["event_name"] == RESOLVE_COMPLETED
    assert payload["event"] == "sla.violation"
    assert payload["subscriber_count"] == 3
    assert payload["duration_ms"] == 12.5


def test_a_failed_resolve_carries_the_error(caplog):
    caplog.set_level(logging.INFO)
    listener = EventListenerLogger("test.event_listener")

    listener.resolve_failed("sla.violation", error="the database is gone")

    payload = _payloads(caplog)[-1]
    assert payload["event_name"] == RESOLVE_FAILED
    assert payload["event"] == "sla.violation"
    assert payload["error"] == "the database is gone"


def test_a_skipped_subscriber_carries_the_subscriber_and_the_reason(caplog):
    caplog.set_level(logging.INFO)
    listener = EventListenerLogger("test.event_listener")

    listener.subscriber_skipped("sla.violation", subscriber_id="w-1", reason="rate_limited")

    payload = _payloads(caplog)[-1]
    assert payload["event_name"] == SUBSCRIBER_SKIPPED
    assert payload["subscriber_id"] == "w-1"
    assert payload["reason"] == "rate_limited"


def test_a_dispatch_line_carries_the_delivery_count(caplog):
    caplog.set_level(logging.INFO)
    listener = EventListenerLogger("test.event_listener")

    listener.dispatch_completed("sla.violation", delivery_count=4)

    payload = _payloads(caplog)[-1]
    assert payload["event_name"] == DISPATCH_COMPLETED
    assert payload["delivery_count"] == 4


def test_every_line_names_its_event(caplog):
    caplog.set_level(logging.INFO)
    listener = EventListenerLogger("test.event_listener")

    listener.resolve_started("sla.violation")
    listener.resolve_completed("sla.violation", subscriber_count=1, duration_ms=1.0)
    listener.resolve_failed("sla.violation", error="boom")
    listener.dispatch_completed("sla.violation", delivery_count=1)
    listener.subscriber_skipped("sla.violation", subscriber_id="w-1", reason="policy")

    payloads = _payloads(caplog)
    assert len(payloads) == 5
    assert all(payload["event_name"] for payload in payloads)
    assert all(payload["event"] == "sla.violation" for payload in payloads)
