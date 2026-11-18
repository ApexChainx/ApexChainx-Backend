"""Event listener utilities and in-process event bus.

This module contains two related APIs used by the codebase/tests:
- DB-backed webhook subscriber resolution (`EventListener(db, ...)`, `load_event_subscribers`)
  for the webhook fan-out path.
- In-process event history/subscription bus (`EventListener(...)` with no DB,
  `event_listener` singleton, `RetryPolicy`, `process_event`, `health`) used by
  events endpoints and retry/health tests.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
_std_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubscriberDecision:
    """The outcome of applying the fan-out policy to a single subscriber."""

    webhook: Webhook
    allowed: bool
    reason: str | None = None


@dataclass
class RetryPolicy:
    """Retry policy for in-process subscriber callbacks."""

    max_attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 5.0
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0:
            raise ValueError("base_delay must be >= 0")
        if self.max_delay < 0:
            raise ValueError("max_delay must be >= 0")

    def delay_for_attempt(self, attempt: int) -> float:
        """Return delay before the next retry after `attempt` (1-based failure)."""
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if self.jitter and delay > 0:
            delay = random.uniform(0.0, delay)
        return min(delay, self.max_delay)


class _InProcessEventBus:
    def __init__(
        self,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._events: list[dict[str, Any]] = []
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], Any]]] = defaultdict(list)
        self._retry = retry_policy or RetryPolicy()
        self._sleep = sleep or time.sleep

    def subscribe(self, topic: str, callback: Callable[[dict[str, Any]], Any]) -> None:
        self._subscribers[topic].append(callback)

    def process_event(self, event: dict[str, Any]) -> dict[str, int]:
        self._events.append(event)
        topic = event.get("topic")
        callbacks = list(self._subscribers.get(topic, [])) + list(self._subscribers.get("*", []))
        delivered = 0
        failed = 0
        for callback in callbacks:
            if self._deliver_with_retry(topic, event, callback):
                delivered += 1
            else:
                failed += 1
        _std_logger.debug(
            "Event processed",
            extra={
                "topic": topic,
                "subscribers_delivered": delivered,
                "subscribers_failed": failed,
            },
        )
        return {"subscribers_delivered": delivered, "subscribers_failed": failed}

    def _deliver_with_retry(self, topic: Any, event: dict[str, Any], callback: Callable[[dict[str, Any]], Any]) -> bool:
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                callback(event)
                return True
            except Exception as exc:
                if attempt >= self._retry.max_attempts:
                    _std_logger.warning(
                        "Subscriber failed after all retry attempts",
                        extra={
                            "topic": topic,
                            "error_type": type(exc).__name__,
                            "attempts": attempt,
                            "callback": getattr(callback, "__name__", repr(callback)),
                        },
                    )
                    return False
                delay = self._retry.delay_for_attempt(attempt)
                _std_logger.warning(
                    "Subscriber raised an exception, retrying with backoff",
                    extra={
                        "topic": topic,
                        "attempt": attempt,
                        "max_attempts": self._retry.max_attempts,
                        "retry_delay_seconds": delay,
                        "callback": getattr(callback, "__name__", repr(callback)),
                    },
                )
                self._sleep(delay)
        return False

    def list_events(self) -> dict[str, Any]:
        return {"events": list(self._events), "total": len(self._events)}

    def health(self) -> dict[str, Any]:
        topics = {event.get("topic") for event in self._events}
        return {
            "status": "ok",
            "total_events": len(self._events),
            "topic_count": len(topics),
        }


class EventListener:
    """Resolves webhook subscribers and/or serves as an in-process event bus."""

    def __init__(
        self,
        db: Session | None = None,
        *,
        repository: WebhookRepository | None = None,
        metrics: EventListenerMetrics | None = None,
        policy: Callable[[WebhookEvent, Webhook], SubscriberDecision] | None = None,
        logger: EventListenerLogger | None = None,
        cache: EventListenerCache | None = None,
        rate_limiter: EventRateLimiter | None = None,
        loader: Callable[[Session, WebhookEvent], list[Webhook]] | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._bus = _InProcessEventBus(retry_policy=retry_policy, sleep=sleep)
        self._retry = self._bus._retry
        self._events = self._bus._events

        self._db = db
        self._repository = (
            repository
            if repository is not None and db is not None
            else (WebhookRepository(db) if db is not None else None)
        )
        self._metrics = metrics if metrics is not None else EventListenerMetrics()
        self._policy = policy
        self._logger = logger if logger is not None else EventListenerLogger(__name__)
        self._cache = cache
        self._rate_limiter = rate_limiter
        self._loader = loader

    # In-process bus API --------------------------------------------------

    def subscribe(self, topic: str, callback: Callable[[dict[str, Any]], Any]) -> None:
        self._bus.subscribe(topic, callback)

    def process_event(self, event: dict[str, Any]) -> dict[str, int]:
        return self._bus.process_event(event)

    def list_events(self) -> dict[str, Any]:
        return self._bus.list_events()

    def health(self) -> dict[str, Any]:
        return self._bus.health()

    # DB subscriber API ---------------------------------------------------

    @staticmethod
    def _event_name(event: WebhookEvent) -> str:
        return event.value

    def _coerce_event(self, event: WebhookEvent | str) -> WebhookEvent:
        if isinstance(event, WebhookEvent):
            return event
        try:
            return WebhookEvent(event)
        except ValueError as exc:
            raise InvalidEventError(f"unsupported event: {event!r}", event=str(event)) from exc

    def _decide(self, event: WebhookEvent, webhook: Webhook) -> SubscriberDecision:
        if self._policy is None:
            return SubscriberDecision(webhook=webhook, allowed=True)
        return self._policy(event, webhook)

    def _store_subscriber_ids(self, event: WebhookEvent, subscribers: list[Webhook]) -> None:
        if self._cache is None:
            return
        try:
            self._cache.set_subscriber_ids(
                self._event_name(event),
                [str(subscriber.id) for subscriber in subscribers],
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "event_listener_cache_unusable",
                event_name="event_listener_cache_unusable",
                event=self._event_name(event),
                error=str(exc),
            )
            increment_counter("event_listener.cache.unavailable")

    def _resolve_subscribers(self, resolved: WebhookEvent) -> list[Webhook]:
        event_name = self._event_name(resolved)
        self._metrics.resolution_started(event_name)
        self._logger.resolve_started(event_name)
        started = time.perf_counter()
        cached_ids = self._cache.get_subscriber_ids(resolved) if self._cache is not None else None
        if cached_ids is not None:
            subscribers = [Webhook(id=subscriber_id) for subscriber_id in cached_ids]
            duration_ms = (time.perf_counter() - started) * 1000
            self._metrics.resolution_completed(event_name, len(subscribers), duration_ms / 1000)
            self._logger.resolve_completed(event_name, len(subscribers), duration_ms)
            return subscribers
        try:
            if self._loader is not None:
                if self._db is None:
                    raise SubscriberResolutionError(
                        "No database session available for subscriber resolution", event=event_name
                    )
                subscribers = self._loader(self._db, resolved)
            elif self._repository is not None:
                subscribers = self._repository.subscribers_for_event(resolved)
            elif self._db is not None:
                subscribers = load_event_subscribers(self._db, resolved)
            else:
                subscribers = []
        except (InvalidEventError, SubscriberResolutionError):
            raise
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

    def subscribers(self, event: WebhookEvent | str) -> list[Webhook]:
        resolved = self._coerce_event(event)
        return self._resolve_subscribers(resolved)

    def _check_rate_limit(self, event: WebhookEvent, subscriber_id: str) -> tuple[bool, str | None]:
        if self._rate_limiter is None:
            return True, None
        if self._rate_limiter.allow(subscriber_id):
            return True, None
        retry_after = 0
        if hasattr(self._rate_limiter, "retry_after_seconds"):
            try:
                retry_after = int(self._rate_limiter.retry_after_seconds(subscriber_id))
            except Exception:
                retry_after = 0
        increment_counter("event_listener.rate_limited", tags={"event": event.value})
        logger.warning(
            "event_listener_rate_limited",
            event_name="event_listener_rate_limited",
            event=event.value,
            subscriber_id=subscriber_id,
            retry_after_seconds=retry_after,
        )
        return False, f"subscriber is over its event budget; retry in {retry_after}s"

    def dispatch_plan(self, event: WebhookEvent | str) -> list[SubscriberDecision]:
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

    def dispatch(self, event: WebhookEvent | str) -> list[SubscriberDecision]:
        decisions = self.dispatch_plan(event)
        allowed = [decision for decision in decisions if decision.allowed]
        self._metrics.dispatch_completed(self._event_name(self._coerce_event(event)), len(allowed))
        return allowed

    def invalidate_cache(self, event: WebhookEvent | str | None = None) -> None:
        if self._cache is None:
            return
        if event is None:
            self._cache.invalidate_all()
            return
        self._cache.invalidate(self._event_name(self._coerce_event(event)))


def load_event_subscribers(db: Session, event: WebhookEvent | str) -> list[Webhook]:
    """Resolve active webhook subscribers for an event from the database."""
    resolved = event if isinstance(event, WebhookEvent) else WebhookEvent(event)
    try:
        rows = db.query(Webhook).filter(Webhook.is_active.is_(True)).filter(Webhook.deleted_at.is_(None)).all()
    except SQLAlchemyError:
        raise
    subscribers: list[Webhook] = []
    for webhook in rows:
        try:
            events = json.loads(webhook.events)
        except Exception:
            continue
        if isinstance(events, list) and resolved.value in events:
            subscribers.append(webhook)
    return subscribers


event_listener = EventListener()
