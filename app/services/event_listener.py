"""The event listener: who hears about a domain event.

``EventListener`` is the single place that answers *who* is subscribed to a
domain event. Every path that emits an event goes through it, so the answer
is the same no matter who asked, and it can be tested once instead of once
per call site.

The listener is deliberately read-only with respect to the database: it
resolves and filters the subscribers, and the delivery pipeline stays
responsible for creating and dispatching the deliveries themselves.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import and_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.webhook import Webhook, WebhookEvent
from app.services.metrics import increment_counter

logger = logging.getLogger(__name__)


class EventListenerError(RuntimeError):
    """Base class for every error raised by the event listener."""

    error_code = "event_listener_error"

    def __init__(self, detail: str, *, event: str | None = None) -> None:
        self.detail = detail
        self.event = event
        super().__init__(detail)


class InvalidEventError(EventListenerError):
    """The event is not something the listener is able to fan out."""

    error_code = "event_listener_invalid_event"


class SubscriberResolutionError(EventListenerError):
    """The subscriber list for an event could not be resolved."""

    error_code = "event_listener_subscriber_resolution_failed"


@dataclass(frozen=True)
class SubscriberDecision:
    """The outcome of applying the fan-out policy to a single subscriber."""

    webhook: Webhook
    allowed: bool
    reason: str | None = None


def load_event_subscribers(db: Session, event: WebhookEvent) -> list[Webhook]:
    """Return the active webhooks that are subscribed to ``event``.

    The tombstone filter is stated explicitly so a future caller that
    reactivates a row cannot resurrect its deliveries (``is_active`` already
    excludes soft-deleted webhooks because delete clears it).

    The exact membership test is applied in Python because the ``events``
    column holds a JSON-encoded list.
    """
    webhooks = (
        db.query(Webhook)
        .filter(
            and_(
                Webhook.is_active.is_(True),
                Webhook.deleted_at.is_(None),
            )
        )
        .all()
    )

    subscribers: list[Webhook] = []
    for webhook in webhooks:
        try:
            events = json.loads(webhook.events)
        except (json.JSONDecodeError, TypeError):
            logger.error("Webhook %s has invalid events JSON, skipping.", webhook.id)
            increment_counter("webhook.events_json.corrupt", tags={"webhook_id": str(webhook.id)})
            continue
        if event.value in events:
            subscribers.append(webhook)
    return subscribers


class EventListener:
    """Resolves the subscribers of a domain event and applies the fan-out policy."""

    def __init__(
        self,
        db: Session,
        *,
        loader: Callable[[Session, WebhookEvent], list[Webhook]] = load_event_subscribers,
        policy: Callable[[WebhookEvent, Webhook], SubscriberDecision] | None = None,
    ) -> None:
        self._db = db
        self._loader = loader
        self._policy = policy

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _event_name(event: WebhookEvent) -> str:
        return event.value

    def _coerce_event(self, event: WebhookEvent | str) -> WebhookEvent:
        """Normalise the event into a :class:`WebhookEvent`.

        Raises:
            InvalidEventError: when the event is not one the listener knows.
        """
        if isinstance(event, WebhookEvent):
            return event
        try:
            return WebhookEvent(event)
        except ValueError as exc:
            raise InvalidEventError(f"unsupported event: {event!r}") from exc

    def _decide(self, event: WebhookEvent, webhook: Webhook) -> SubscriberDecision:
        """Apply the fan-out policy to one subscriber."""
        if self._policy is None:
            return SubscriberDecision(webhook=webhook, allowed=True)
        return self._policy(event, webhook)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribers(self, event: WebhookEvent | str) -> list[Webhook]:
        """Return the active subscribers of ``event``.

        Raises:
            InvalidEventError: when the event is not a known event.
            SubscriberResolutionError: when the subscriber list cannot be read.
        """
        resolved = self._coerce_event(event)
        try:
            return self._loader(self._db, resolved)
        except SQLAlchemyError as exc:
            logger.error("Could not resolve the subscribers of %s: %s", self._event_name(resolved), exc)
            raise SubscriberResolutionError(
                "Could not resolve the subscribers of the event.",
                event=self._event_name(resolved),
            ) from exc

    def dispatch_plan(self, event: WebhookEvent | str) -> list[SubscriberDecision]:
        """Return the subscribers of ``event`` with the fan-out policy applied.

        A subscriber that is not allowed to receive the event is reported
        with ``allowed=False`` instead of being silently dropped, so the
        caller can log, count and (if it wants to) retry it later.
        """
        resolved = self._coerce_event(event)
        return [self._decide(resolved, webhook) for webhook in self.subscribers(resolved)]
