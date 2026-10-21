"""Unit tests for the event listener (issue #734).

The event listener decides which registered webhooks hear about a domain
event. It had no tests of its own, so a change to the subscription filter or
to the fan-out policy could only be caught by a test that happened to emit
an event.

These tests exercise the listener in isolation: no database, no HTTP, no
Celery -- just the decision the listener makes.
"""

import json

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models.webhook import WebhookEvent
from app.services.event_listener import (
    EventListener,
    InvalidEventError,
    SubscriberDecision,
    SubscriberResolutionError,
    load_event_subscribers,
)


class FakeWebhook:
    """The bits of a webhook row the listener reads."""

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


class TestSubscribers:
    def test_only_the_subscribers_of_the_event_are_returned(self):
        session = FakeSession(
            [
                FakeWebhook("w-1", ["sla.violation"]),
                FakeWebhook("w-2", ["sla.resolved"]),
                FakeWebhook("w-3", ["sla.warning", "sla.violation"]),
            ]
        )

        subscribers = EventListener(session).subscribers(WebhookEvent.SLA_VIOLATION)

        assert [webhook.id for webhook in subscribers] == ["w-1", "w-3"]

    def test_an_event_without_subscribers_returns_an_empty_list(self):
        session = FakeSession([FakeWebhook("w-1", ["sla.resolved"])])

        assert EventListener(session).subscribers(WebhookEvent.SLA_VIOLATION) == []

    def test_a_row_with_a_corrupt_events_column_is_skipped(self):
        corrupt = FakeWebhook("w-1", [])
        corrupt.events = "not json"
        session = FakeSession([corrupt, FakeWebhook("w-2", ["sla.violation"])])

        subscribers = EventListener(session).subscribers(WebhookEvent.SLA_VIOLATION)

        assert [webhook.id for webhook in subscribers] == ["w-2"]

    def test_the_event_may_be_given_as_its_string_value(self):
        session = FakeSession([FakeWebhook("w-1", ["sla.violation"])])

        subscribers = EventListener(session).subscribers("sla.violation")

        assert [webhook.id for webhook in subscribers] == ["w-1"]

    def test_an_unknown_event_is_rejected(self):
        session = FakeSession([])

        with pytest.raises(InvalidEventError):
            EventListener(session).subscribers("sla.exploded")

    def test_a_database_failure_is_reported_as_a_resolution_error(self):
        def exploding_loader(db, event):
            raise SQLAlchemyError("the database is gone")

        session = FakeSession([])

        with pytest.raises(SubscriberResolutionError) as exc_info:
            EventListener(session, loader=exploding_loader).subscribers(WebhookEvent.SLA_VIOLATION)

        assert exc_info.value.event == "sla.violation"

    def test_the_resolution_asks_the_database_once(self):
        session = FakeSession([FakeWebhook("w-1", ["sla.violation"])])

        EventListener(session).subscribers(WebhookEvent.SLA_VIOLATION)

        assert session.queries == 1


class TestDispatchPlan:
    def test_every_subscriber_is_allowed_by_default(self):
        session = FakeSession([FakeWebhook("w-1", ["sla.violation"])])

        decisions = EventListener(session).dispatch_plan(WebhookEvent.SLA_VIOLATION)

        assert len(decisions) == 1
        assert decisions[0].allowed is True
        assert decisions[0].reason is None
        assert decisions[0].webhook.id == "w-1"

    def test_a_refused_subscriber_is_reported_instead_of_being_dropped(self):
        session = FakeSession([FakeWebhook("w-1", ["sla.violation"])])

        def refusing_policy(event, webhook):
            return SubscriberDecision(webhook=webhook, allowed=False, reason="rate_limited")

        decisions = EventListener(session, policy=refusing_policy).dispatch_plan(WebhookEvent.SLA_VIOLATION)

        assert decisions[0].allowed is False
        assert decisions[0].reason == "rate_limited"


class TestLoader:
    def test_the_loader_is_the_default_resolver(self):
        session = FakeSession([FakeWebhook("w-1", ["sla.violation"])])

        subscribers = load_event_subscribers(session, WebhookEvent.SLA_VIOLATION)

        assert [webhook.id for webhook in subscribers] == ["w-1"]
