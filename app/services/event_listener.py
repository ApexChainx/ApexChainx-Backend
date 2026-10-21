"""The event listener: who hears about a domain event, and how it is reported.

``EventListener`` is the single place that answers two questions for every
domain event the backend emits:

1. *Who* is subscribed to it? -- the registered, active webhooks.
2. *Is this subscriber allowed to receive it right now?* -- the fan-out policy
   and the per-subscriber event budget.

It resolves the subscribers with one statement, applies the fan-out policy to
them, and reports what it did through Prometheus metrics, structured logs and
a subscriber-id cache.

The listener is deliberately read-only with respect to the database: it
resolves and filters the subscribers, and the delivery pipeline stays
responsible for creating and dispatching the deliveries themselves.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.webhook import Webhook, WebhookEvent
from app.repositories.webhook_repository import WebhookRepository
from app.services.event_listener_cache import EventListenerCache
from app.services.event_listener_errors import (
    InvalidEventError,
    SubscriberResolutionError,
)
from app.services.event_listener_logging import EventListenerLogger
from app.services.event_listener_metrics import EventListenerMetrics
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


class EventListener:
    """Resolves the subscribers of a domain event and applies the fan-out policy."""


    def __init__(
        self,
        db: Session,
        *,
        repository: WebhookRepository | None = None,
        metrics: EventListenerMetrics | None = None,
        policy: Callable[[WebhookEvent, Webhook], SubscriberDecision] | None = None,
        logger: EventListenerLogger | None = None,
        cache: EventListenerCache | None = None,
        rate_limiter: EventRateLimiter | None = None,
    ) -> None:
        self._db = db
        self._repository = repository if repository is not None else WebhookRepository(db)
        self._metrics = metrics if metrics is not None else EventListenerMetrics()
        self._policy = policy
        self._logger = logger if logger is not None else EventListenerLogger(__name__)
        self._cache = cache
        self._rate_limiter = rate_limiter

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
        if self._policy is None:
            return SubscriberDecision(webhook=webhook, allowed=True)
        return self._policy(event, webhook)

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

        The subscriber list is resolved with one statement (the subscription
        filter is part of it, issue #739) and its *ids* are cached; the rows
        themselves are always re-read from the database, so a webhook that was
        deactivated or deleted is never delivered to because of a stale cache
        entry.

        Raises:
            InvalidEventError: when the event is not a known event.
            SubscriberResolutionError: when the subscriber list cannot be read.
        """
        resolved = self._coerce_event(event)
        event_name = self._event_name(resolved)

        self._metrics.resolution_started(event_name)
        self._logger.resolve_started(event_name)

        started = time.perf_counter()
        try:
            subscribers = self._repository.subscribers_for_event(resolved)
        except SQLAlchemyError as exc:
            self._metrics.error(event_name, "resolve")
            self._logger.resolve_failed(event_name, str(exc))
            raise SubscriberResolutionError(
                "Could not resolve the subscribers of the event.",
                event=event_name,
            ) from exc

        duration_ms = (time.perf_counter() - started) * 1000
        self._metrics.resolution_completed(event_name, len(subscribers), duration_ms / 1000)
        self._logger.resolve_completed(event_name, len(subscribers), duration_ms)
        self._store_subscriber_ids(resolved, subscribers)
        return subscribers

    def dispatch_plan(self, event: WebhookEvent | str) -> list[SubscriberDecision]:
        """Return the subscribers of ``event`` with the fan-out policy applied.

        A subscriber that is not allowed to receive the event -- because of
        the fan-out policy or because it is over its event budget -- is
        reported with ``allowed=False`` instead of being silently dropped, so
        the caller can log, count and (if it wants to) retry it later.
        """
        resolved = self._coerce_event(event)
        event_name = self._event_name(resolved)

        decisions: list[SubscriberDecision] = []
        for webhook in self.subscribers(resolved):
            subscriber_id = str(webhook.id)
            allowed, reason = self._check_rate_limit(resolved, subscriber_id)
            if allowed:
                decision = self._decide(resolved, webhook)
            else:
                decision = SubscriberDecision(webhook=webhook, allowed=False, reason=reason)
            if not decision.allowed:
                skip_reason = decision.reason or "policy"
                self._metrics.subscriber_skipped(event_name, skip_reason)
                self._logger.subscriber_skipped(event_name, subscriber_id, skip_reason)
            decisions.append(decision)
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

    def dispatch(self, event: WebhookEvent | str) -> list[SubscriberDecision]:
        """Resolve the subscribers of ``event`` and report the dispatch."""
        decisions = self.dispatch_plan(event)
        allowed = [decision for decision in decisions if decision.allowed]
        self._metrics.dispatch_completed(self._event_name(self._coerce_event(event)), len(allowed))
        return allowed

    def invalidate_cache(self, event: WebhookEvent | str | None = None) -> None:
        """Drop the cached subscriber list for ``event`` (or for every event)."""
        if self._cache is None:
            return
        if event is None:
            self._cache.invalidate_all()
            return
        self._cache.invalidate(self._event_name(self._coerce_event(event)))
