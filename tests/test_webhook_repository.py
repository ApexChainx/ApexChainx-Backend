"""Tests for the webhook read path used by the event listener (issue #739).

The subscription test used to happen in Python: the database returned every
active registration and the listener JSON-parsed each row to find out whether
it was subscribed. These tests pin that the test now happens in SQL, and
that the exact membership check still runs afterwards.
"""

import json

import pytest

from app.models.webhook import WebhookEvent
from app.repositories.webhook_repository import (
    InvalidEventValueError,
    WebhookRepository,
    event_pattern,
    is_subscribed,
    subscriber_filter,
)


class FakeWebhook:
    """The bits of a webhook row the repository reads."""

    def __init__(self, webhook_id, events):
        self.id = webhook_id
        self.events = json.dumps(events)


class FakeSession:
    """A session stand-in that records the filters and answers with rows."""

    def __init__(self, webhooks):
        self._webhooks = list(webhooks)
        self.filters = []

    def query(self, entity):
        return self

    def filter(self, *args, **kwargs):
        self.filters.extend(args)
        return self

    def all(self):
        return list(self._webhooks)


def test_the_subscription_filter_is_part_of_the_statement():
    clause = subscriber_filter(WebhookEvent.SLA_VIOLATION)
    sql = str(clause).upper()

    assert "LIKE" in sql
    assert "IS NULL" in sql
    # The event value is a bound parameter, not a literal in the statement.
    assert '"sla.violation"' not in str(clause)


def test_the_pattern_matches_the_quoted_event_name():
    assert event_pattern("sla.violation") == '%"sla.violation"%'


@pytest.mark.parametrize("value", ["", "sla violation", "sla.violation%", "sla_violation", "SLA.violation"])
def test_an_event_value_that_is_not_a_plain_token_is_rejected(value):
    with pytest.raises(InvalidEventValueError):
        event_pattern(value)


def test_only_the_subscribers_of_the_event_are_returned():
    session = FakeSession(
        [
            FakeWebhook("w-1", ["sla.violation"]),
            FakeWebhook("w-2", ["sla.resolved"]),
            FakeWebhook("w-3", ["sla.warning", "sla.violation"]),
        ]
    )

    subscribers = WebhookRepository(session).subscribers_for_event(WebhookEvent.SLA_VIOLATION)

    assert [webhook.id for webhook in subscribers] == ["w-1", "w-3"]


def test_the_resolution_is_a_single_statement():
    session = FakeSession([FakeWebhook("w-1", ["sla.violation"])])

    WebhookRepository(session).subscribers_for_event(WebhookEvent.SLA_VIOLATION)

    assert len(session.filters) == 1


def test_a_row_with_a_corrupt_events_column_is_not_a_subscriber():
    corrupt = FakeWebhook("w-1", [])
    corrupt.events = "not json"
    session = FakeSession([corrupt, FakeWebhook("w-2", ["sla.violation"])])

    subscribers = WebhookRepository(session).subscribers_for_event(WebhookEvent.SLA_VIOLATION)

    assert [webhook.id for webhook in subscribers] == ["w-2"]


def test_a_row_whose_events_column_is_not_a_list_is_not_a_subscriber():
    odd = FakeWebhook("w-1", [])
    odd.events = json.dumps({"sla.violation": True})
    session = FakeSession([odd])

    assert WebhookRepository(session).subscribers_for_event(WebhookEvent.SLA_VIOLATION) == []


def test_is_subscribed_is_exact():
    webhook = FakeWebhook("w-1", ["sla.violation"])

    assert is_subscribed(webhook, WebhookEvent.SLA_VIOLATION) is True
    assert is_subscribed(webhook, WebhookEvent.SLA_RESOLVED) is False
