"""Tests for the event listener's fan-out policy (issues #736, #740, #741).

The listener is the single place that answers "who hears about this event".
These tests pin the three behaviours that make it safe to route every event
through it: a broken subscriber never takes the event down, a cached
subscriber list is still re-validated against the database, and a subscriber
that is over its budget is reported rather than silently dropped.
"""

import json

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models.webhook import WebhookEvent
from app.services.event_listener import EventListener
from app.services.event_listener_errors import InvalidEventError, SubscriberResolutionError
from app.services.event_listener_rate_limit import EventRateLimiter


class FakeWebhook:
    """The bits of a webhook row the listener actually reads."""

    def __init__(self, webhook_id, events, is_active=True):
        self.id = webhook_id
        self.events = json.dumps(events)
        self.is_active = is_active


class FakeSession:
    """A session stand-in that answers every query with the same rows."""

    def __init__(self, webhooks):
        self._webhooks = list(webhooks)
        self.queries = 0

    def query(self, entity):
        self.queries += 1
        return self

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._webhooks)


class StubCache:
    """A cache stand-in that returns a fixed answer."""

    def __init__(self, subscriber_ids):
        self._subscriber_ids = subscriber_ids
        self.stored = []

    def get_subscriber_ids(self, event):
        return self._subscriber_ids

    def set_subscriber_ids(self, event, subscriber_ids):
        self.stored.append((event, list(subscriber_ids)))

    def invalidate(self, event):
        pass

    def invalidate_all(self):
        pass


def test_only_the_subscribers_of_the_event_are_returned():
    session = FakeSession(
        [
            FakeWebhook("w-1", ["sla.violation"]),
            FakeWebhook("w-2", ["sla.resolved"]),
            FakeWebhook("w-3", ["sla.warning", "sla.violation"]),
        ]
    )

    subscribers = EventListener(session).subscribers(WebhookEvent.SLA_VIOLATION)

    assert [webhook.id for webhook in subscribers] == ["w-1", "w-3"]


def test_a_row_with_a_corrupt_events_column_is_skipped():
    corrupt = FakeWebhook("w-1", [])
    corrupt.events = "not json"
    session = FakeSession([corrupt, FakeWebhook("w-2", ["sla.violation"])])

    subscribers = EventListener(session).subscribers(WebhookEvent.SLA_VIOLATION)

    assert [webhook.id for webhook in subscribers] == ["w-2"]


def test_an_unknown_event_is_rejected():
    session = FakeSession([])

    with pytest.raises(InvalidEventError):
        EventListener(session).subscribers("sla.exploded")


def test_a_database_failure_is_reported_as_a_resolution_error():
    def exploding_loader(db, event):
        raise SQLAlchemyError("the database is gone")

    session = FakeSession([])

    with pytest.raises(SubscriberResolutionError):
        EventListener(session, loader=exploding_loader).subscribers(WebhookEvent.SLA_VIOLATION)


def test_a_cached_subscriber_list_skips_the_resolver():
    def exploding_loader(db, event):
        raise AssertionError("the resolver must not run on a cache hit")

    session = FakeSession([FakeWebhook("w-1", ["sla.violation"])])
    cache = StubCache(["w-1"])

    subscribers = EventListener(session, cache=cache, loader=exploding_loader).subscribers(WebhookEvent.SLA_VIOLATION)

    assert [webhook.id for webhook in subscribers] == ["w-1"]


def test_a_cache_miss_falls_back_to_the_resolver():
    session = FakeSession([FakeWebhook("w-1", ["sla.violation"])])
    cache = StubCache(None)

    subscribers = EventListener(session, cache=cache).subscribers(WebhookEvent.SLA_VIOLATION)

    assert [webhook.id for webhook in subscribers] == ["w-1"]
    # The resolved list is written back so the next emission is a hit.
    assert cache.stored == [("sla.violation", ["w-1"])]


def test_a_subscriber_over_its_budget_is_reported_and_not_allowed():
    session = FakeSession([FakeWebhook("w-1", ["sla.violation"])])
    limiter = EventRateLimiter(None, limit=1, window_seconds=60)

    listener = EventListener(session, rate_limiter=limiter)
    first = listener.dispatch_plan(WebhookEvent.SLA_VIOLATION)
    second = listener.dispatch_plan(WebhookEvent.SLA_VIOLATION)

    assert first[0].allowed is True
    assert second[0].allowed is False
    assert second[0].reason is not None
