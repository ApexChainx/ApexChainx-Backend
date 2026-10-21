"""The event listener: who hears about a domain event, and how often.

``EventListener`` is the single place that answers two questions for every
domain event the backend emits:

1. *Who* is subscribed to it? -- the registered, active webhooks.
2. *Is this subscriber allowed to receive it right now?* -- the fan-out budget.

Both answers used to be spread across the call sites that emit events, which
is why the same event emitted from three different places could reach three
different sets of subscribers.

The listener is deliberately read-only with respect to the database: it
resolves and filters subscribers, and the delivery pipeline stays responsible
for creating and dispatching the deliveries themselves.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import and_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.webhook import Webhook, WebhookEvent
from app.services.event_listener_cache import EventListenerCache
from app.services.event_listener_errors import (
    InvalidEventError,
    SubscriberResolutionError,
)
from app.services.event_listener_rate_limit import EventRateLimiter
from app.services.metrics import increment_counter
from app.utils.logging import get_structured_logger

logger = get_structured_logger(__name__)


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

    The exact membership test is still applied in Python because the ``events``
    column holds a JSON-encoded list; what changed is that the database no
    longer has to hand back every active row just so Python can throw most of
    them away.
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
            logger.error(
                "webhook_events_json_corrupt",
                event_name="webhook_events_json_corrupt",
                webhook_id=str(webhook.id),
            )
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
        cache: EventListenerCache | None = None,
        rate_limiter: EventRateLimiter | None = None,
        loader: Callable[[Session, WebhookEvent], list[Webhook]] = load_event_subscribers,
    ) -> None:
        self._db = db
        self._cache = cache
        self._rate_limiter = rate_limiter
        self._loader = loader

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

    def _load_by_ids(self, subscriber_ids: list[str]) -> list[Webhook]:
        """Re-read the cached subscriber ids from the database."""
        if not subscriber_ids:
            return []
        rows = (
            self._db.query(Webhook)
            .filter(
                and_(
                    Webhook.id.in_(subscriber_ids),
                    Webhook.is_active.is_(True),
                    Webhook.deleted_at.is_(None),
                )
            )
            .all()
        )
        # Keep the cached order so a subscriber's position in the fan-out does
        # not change between two emissions of the same event.
        order = {subscriber_id: index for index, subscriber_id in enumerate(subscriber_ids)}
        return sorted(rows, key=lambda row: order.get(str(row.id), len(order)))

    def _cached_subscriber_ids(self, event: WebhookEvent) -> list[str] | None:
        if self._cache is None:
            return None
        try:
            return self._cache.get_subscriber_ids(self._event_name(event))
        except Exception as exc:  # pragma: no cover - defensive: a cache must never break fan-out
            logger.warning(
                "event_listener_cache_unusable",
                event_name="event_listener_cache_unusable",
                event=self._event_name(event),
                error=str(exc),
            )
            increment_counter("event_listener.cache.unavailable")
            return None

    def _store_subscriber_ids(self, event: WebhookEvent, subscribers: list[Webhook]) -> None:
        if self._cache is None:
            return
        try:
            self._cache.set_subscriber_ids(
                self._event_name(event),
                [str(subscriber.id) for subscriber in subscribers],
            )
        except Exception as exc:  # pragma: no cover - defensive: a cache must never break fan-out
            logger.warning(
                "event_listener_cache_unusable",
                event_name="event_listener_cache_unusable",
                event=self._event_name(event),
                error=str(exc),
            )
            increment_counter("event_listener.cache.unavailable")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribers(self, event: WebhookEvent | str) -> list[Webhook]:
        """Return the active subscribers of ``event``.

        The subscriber *ids* are cached, but the rows themselves are always
        re-read from the database, so a webhook that was deactivated or
        deleted is never delivered to because of a stale cache entry.

        Raises:
            InvalidEventError: when the event is not a known event.
            SubscriberResolutionError: when the subscriber list cannot be read.
        """
        resolved = self._coerce_event(event)

        cached_ids = self._cached_subscriber_ids(resolved)
        if cached_ids is not None:
            return self._load_by_ids(cached_ids)

        try:
            subscribers = self._loader(self._db, resolved)
        except SQLAlchemyError as exc:
            logger.error(
                "event_listener_subscriber_resolution_failed",
                event_name="event_listener_subscriber_resolution_failed",
                event=self._event_name(resolved),
                error=str(exc),
            )
            increment_counter("event_listener.subscriber_resolution_failed")
            raise SubscriberResolutionError(
                "Could not resolve the subscribers of the event.",
                event=self._event_name(resolved),
            ) from exc

        self._store_subscriber_ids(resolved, subscribers)
        return subscribers

    def dispatch_plan(self, event: WebhookEvent | str) -> list[SubscriberDecision]:
        """Return the subscribers of ``event`` with the fan-out policy applied.

        A subscriber that is over its event budget is reported with
        ``allowed=False`` instead of being silently dropped, so the caller can
        log, count and (if it wants to) retry it later.
        """
        resolved = self._coerce_event(event)
        decisions: list[SubscriberDecision] = []
        for webhook in self.subscribers(resolved):
            subscriber_id = str(webhook.id)
            allowed, reason = self._check_rate_limit(resolved, subscriber_id)
            decisions.append(SubscriberDecision(webhook=webhook, allowed=allowed, reason=reason))
        return decisions

    def _check_rate_limit(self, event: WebhookEvent, subscriber_id: str) -> tuple[bool, str | None]:
        if self._rate_limiter is None:
            return True, None
        if self._rate_limiter.allow(subscriber_id):
            return True, None
        retry_after = self._rate_limiter.retry_after_seconds(subscriber_id)
        increment_counter("event_listener.rate_limited", tags={"event": event.value})
        logger.warning(
            "event_listener_rate_limited",
            event_name="event_listener_rate_limited",
            event=event.value,
            subscriber_id=subscriber_id,
            retry_after_seconds=retry_after,
        )
        return False, f"subscriber is over its event budget; retry in {retry_after}s"

    def invalidate_cache(self, event: WebhookEvent | str | None = None) -> None:
        """Drop the cached subscriber list for ``event`` (or for every event)."""
        if self._cache is None:
            return
        if event is None:
            self._cache.invalidate_all()
            return
        self._cache.invalidate(self._event_name(self._coerce_event(event)))
