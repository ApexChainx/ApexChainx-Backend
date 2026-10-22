"""Read paths for the webhooks table (issue #739).

The event listener used to load every active webhook and decide in Python
whether it was subscribed to the event: the database handed back a row per
registration, and the row was JSON-parsed in the request path before most
of it was thrown away. During an incident, when the same event is emitted
once per affected device, that is a lot of rows transferred and parsed for
nothing.

The subscription test is pushed into SQL here. The ``events`` column holds a
JSON-encoded list of event names, so an exact, quoted match on the event
value selects the candidate rows in the database. The exact membership test
is still applied in Python afterwards, because the SQL match is a substring
match and only the parsed list knows for certain.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.webhook import Webhook, WebhookEvent

# The event names come from a fixed enum, but the SQL pattern is built from
# them, so they are checked against a strict allowlist first: a value that is
# not a plain lowercase token could otherwise carry LIKE metacharacters into
# the statement. ``_`` is excluded on purpose -- it matches any single
# character in a LIKE pattern.
EVENT_VALUE_PATTERN = re.compile(r"\A[a-z0-9]+(?:[.-][a-z0-9]+)*\Z")


class InvalidEventValueError(ValueError):
    """The event value is not a plain lowercase token."""


def event_pattern(event_value: str) -> str:
    """Return the SQL pattern that selects the subscribers of ``event_value``.

    The pattern matches the *quoted* event name, so ``sla.violation`` does not
    also match a hypothetical ``sla.violation.extra``.

    Raises:
        InvalidEventValueError: when the value is not a plain lowercase token.
    """
    if not isinstance(event_value, str) or not EVENT_VALUE_PATTERN.match(event_value):
        raise InvalidEventValueError(f"unsupported event value: {event_value!r}")
    return f'%"{event_value}"%'


def is_subscribed(webhook: Webhook, event: WebhookEvent) -> bool:
    """Return whether ``webhook`` is subscribed to ``event``.

    The ``events`` column is a JSON-encoded list of event names. A row whose
    value cannot be parsed is not subscribed to anything.
    """
    try:
        events = json.loads(webhook.events)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(events, list):
        return False
    return event.value in events


def subscriber_filter(event: WebhookEvent):
    """Return the SQL filter that selects the subscribers of ``event``.

    It is one clause on purpose: the tombstone filter, the active filter and
    the subscription filter belong together, and stating them separately
    means the database is asked several times for the same thing.
    """
    return and_(
        Webhook.is_active.is_(True),
        Webhook.deleted_at.is_(None),
        Webhook.events.like(event_pattern(event.value)),
    )


class WebhookRepository:
    """Read access to the registrations the event listener cares about."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def subscribers_for_event(self, event: WebhookEvent) -> list[Webhook]:
        """Return the active webhooks that are subscribed to ``event``.

        One statement, one round trip. The subscription filter is part of
        the statement, so the database returns the candidate rows instead of
        every registration.
        """
        rows = self.db.query(Webhook).filter(subscriber_filter(event)).all()
        return [row for row in rows if is_subscribed(row, event)]

    def count_subscribers_for_event(self, event: WebhookEvent) -> int:
        """Return how many active webhooks are subscribed to ``event``."""
        return len(self.subscribers_for_event(event))
