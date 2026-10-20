"""The event listener: who hears about a domain event, and how it is reported.

``EventListener`` is the single place that answers *who* is subscribed to a
domain event. It resolves the subscribers with one statement, applies the
fan-out policy to them, and reports what it did through Prometheus metrics
and structured logs.

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
from app.services.event_listener_logging import EventListenerLogger
from app.services.event_listener_metrics import EventListenerMetrics


@dataclass(frozen=True)
class SubscriberDecision:
    """The outcome of applying the fan-out policy to a single subscriber."""

    webhook: Webhook
    allowed: bool
    reason: str | None = None


class SubscriberResolutionError(RuntimeError):
    """The subscriber list for an event could not be resolved."""

    error_code = "event_listener_subscriber_resolution_failed"

    def __init__(self, detail: str, *, event: str | None = None) -> None:
        self.detail = detail
        self.event = event
        super().__init__(detail)


class EventListener:
    """Resolves the subscribers of a domain event and reports what it did."""

    def __init__(
        self,
        db: Session,
        *,
        repository: WebhookRepository | None = None,
        metrics: EventListenerMetrics | None = None,
        policy: Callable[[WebhookEvent, Webhook], SubscriberDecision] | None = None,
        logger: EventListenerLogger | None = None,
    ) -> None:
        self._db = db
        self._repository = repository if repository is not None else WebhookRepository(db)
        self._metrics = metrics if metrics is not None else EventListenerMetrics()
        self._policy = policy
        self._logger = logger if logger is not None else EventListenerLogger(__name__)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _event_name(event: WebhookEvent) -> str:
        return event.value

    def _coerce_event(self, event: WebhookEvent | str) -> WebhookEvent:
        """Normalise the event into a :class:`WebhookEvent`.

        Raises:
            ValueError: when the event is not one the listener knows.
        """
        if isinstance(event, WebhookEvent):
            return event
        return WebhookEvent(event)

    def _decide(self, event: WebhookEvent, webhook: Webhook) -> SubscriberDecision:
        if self._policy is None:
            return SubscriberDecision(webhook=webhook, allowed=True)
        return self._policy(event, webhook)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribers(self, event: WebhookEvent | str) -> list[Webhook]:
        """Return the active subscribers of ``event``.

        The resolution is one statement: the subscription filter is part of
        it, so the database returns the candidate rows instead of every
        registration (issue #739).

        Raises:
            ValueError: when the event is not a known event.
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
        return subscribers

    def dispatch_plan(self, event: WebhookEvent | str) -> list[SubscriberDecision]:
        """Return the subscribers of ``event`` with the fan-out policy applied.

        A subscriber that is not allowed to receive the event is reported
        with ``allowed=False`` instead of being silently dropped, so the
        caller can log, count and (if it wants to) retry it later.
        """
        resolved = self._coerce_event(event)
        event_name = self._event_name(resolved)

        decisions: list[SubscriberDecision] = []
        for webhook in self.subscribers(resolved):
            decision = self._decide(resolved, webhook)
            if not decision.allowed:
                reason = decision.reason or "policy"
                self._metrics.subscriber_skipped(event_name, reason)
                self._logger.subscriber_skipped(event_name, str(webhook.id), reason)
            decisions.append(decision)
        return decisions

    def dispatch(self, event: WebhookEvent | str) -> list[SubscriberDecision]:
        """Resolve the subscribers of ``event`` and report the dispatch."""
        decisions = self.dispatch_plan(event)
        allowed = [decision for decision in decisions if decision.allowed]
        self._metrics.dispatch_completed(self._event_name(self._coerce_event(event)), len(allowed))
        return allowed
