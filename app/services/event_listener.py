"""EventListener service.

Provides an in-process, topic-based event bus with paginated history
retrieval.  Events are stored in an ordered list; subscribers are callable
hooks registered per topic (or the wildcard ``"*"`` to receive every event).

Usage example::

    listener = EventListener()
    listener.subscribe("outage.created", my_callback)

    listener.process_event({"topic": "outage.created", "payload": {...}})

    page = listener.list_events(topic="outage.created", page=1, page_size=20)
    # {"items": [...], "total": 1, "page": 1, "page_size": 20, "pages": 1}

Subscriber delivery is retried with bounded exponential backoff (see
:class:`RetryPolicy`), so a subscriber that fails transiently is given a few
more chances before the event is reported as undeliverable to it.

The HTTP surface for this service lives in
``app/api/v1/endpoints/event_listener.py``.
"""

from __future__ import annotations

import logging
import random
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Topic that receives every event, regardless of the event's own topic.
WILDCARD_TOPIC = "*"


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff applied when a subscriber raises.

    The delay before retry *n* (1-based) is ``min(base_delay * 2 ** (n - 1),
    max_delay)``.  When ``jitter`` is enabled the delay is drawn uniformly
    from ``[0, delay]`` ("full jitter") so that a fleet of listeners does not
    retry in lockstep.

    Attributes:
        max_attempts: Total delivery attempts per subscriber, including the
            first one.  Must be at least 1.
        base_delay: Delay in seconds before the first retry.
        max_delay: Upper bound in seconds for any single backoff delay.
        jitter: Whether to apply full jitter to each delay.

    Raises:
        ValueError: When ``max_attempts`` is below 1 or a delay is negative.
    """

    max_attempts: int = 3
    base_delay: float = 0.05
    max_delay: float = 1.0
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must not be negative")

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the delay in seconds to wait after failed *attempt*.

        Args:
            attempt: 1-based number of the attempt that just failed.

        Returns:
            The capped (and optionally jittered) delay in seconds.
        """
        delay = min(self.base_delay * (2 ** max(0, attempt - 1)), self.max_delay)
        if self.jitter and delay > 0:
            # nosec B311 - retry jitter is a load-spreading measure, not security
            delay = random.uniform(0.0, delay)
        return delay


class EventListener:
    """In-process event bus with paginated history retrieval.

    The listener keeps two pieces of state:

    * ``_events`` — every event that has been processed, in insertion order.
    * ``_subscribers`` — a mapping of topic to the callables registered for it.
    * ``_retry`` — the :class:`RetryPolicy` used for subscriber delivery.

    Instances are cheap to create, which makes it easy to build an isolated
    listener per request or per test.  The application uses the module-level
    :data:`event_listener` singleton.
    """

    def __init__(
        self,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """Create an empty event bus.

        Args:
            retry_policy: Backoff settings used when a subscriber raises.
                Defaults to :class:`RetryPolicy` with its stock values.
            sleep: Function used to wait between delivery attempts.  Injected
                so tests can record backoff delays without actually sleeping.
                Defaults to :func:`time.sleep`.
        """
        # All events, in insertion order.
        self._events: list[dict[str, Any]] = []
        # Topic → list of subscriber callables.  "*" catches every topic.
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)
        self._retry = retry_policy or RetryPolicy()
        self._sleep = sleep or time.sleep

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(
        self,
        topic: str,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """Register *callback* to receive events for *topic*.

        Args:
            topic: The topic to subscribe to.  Pass
                :data:`WILDCARD_TOPIC` (``"*"``) to receive every event
                regardless of topic.
            callback: A callable that accepts the event dict as its sole
                argument.  It is invoked synchronously inside
                :meth:`process_event`, and retried per the listener's
                :class:`RetryPolicy` when it raises.  Exhausted retries are
                logged and do not prevent delivery to other subscribers.
        """
        self._subscribers[topic].append(callback)
        logger.debug(
            "Subscriber registered",
            extra={
                "topic": topic,
                "callback": getattr(callback, "__name__", repr(callback)),
            },
        )

    def unsubscribe(
        self,
        topic: str,
        callback: Callable[[dict[str, Any]], None],
    ) -> bool:
        """Remove *callback* from *topic*'s subscriber list.

        Args:
            topic: The topic from which to remove the subscriber.
            callback: The previously registered callable.

        Returns:
            ``True`` if the callback was found and removed, ``False`` if it
            was never registered (or has already been removed).
        """
        subscribers = self._subscribers.get(topic, [])
        try:
            subscribers.remove(callback)
        except ValueError:
            return False

        logger.debug(
            "Subscriber removed",
            extra={
                "topic": topic,
                "callback": getattr(callback, "__name__", repr(callback)),
            },
        )
        return True

    # ------------------------------------------------------------------
    # Event processing
    # ------------------------------------------------------------------

    def process_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Record *event* and dispatch it to all matching subscribers.

        The event dict is augmented with a ``received_at`` ISO-8601 timestamp
        if one is not already present.  Subscribers registered for the
        event's topic are notified first, followed by wildcard subscribers.
        Each delivery is retried with exponential backoff when the subscriber
        raises (issue #745); a subscriber that keeps failing is logged and
        skipped so the remaining subscribers still receive the event.

        Args:
            event: A mapping that should include at minimum a ``"topic"``
                key.  Events without a topic are stored under ``"unknown"``.

        Returns:
            The stored event dict (with the ``received_at`` field set).
        """
        topic = event.get("topic", "unknown")
        stored = {
            **event,
            "received_at": event.get(
                "received_at",
                datetime.now(tz=UTC).isoformat(),
            ),
        }
        self._events.append(stored)

        recipients = self._recipients(topic)
        delivered = sum(1 for callback in recipients if self._deliver(callback, stored, topic))

        logger.debug(
            "Event processed",
            extra={
                "topic": topic,
                "subscribers_notified": len(recipients),
                "subscribers_delivered": delivered,
                "subscribers_failed": len(recipients) - delivered,
            },
        )
        return stored

    def _recipients(self, topic: str) -> list[Callable[[dict[str, Any]], None]]:
        """Return the subscribers for *topic*, wildcard subscribers last."""
        return list(self._subscribers.get(topic, [])) + list(self._subscribers.get(WILDCARD_TOPIC, []))

    def _deliver(
        self,
        callback: Callable[[dict[str, Any]], None],
        event: dict[str, Any],
        topic: str,
    ) -> bool:
        """Deliver *event* to *callback*, retrying with exponential backoff.

        Args:
            callback: The subscriber to invoke.
            event: The stored event record passed to the subscriber.
            topic: The event topic, used for structured log fields.

        Returns:
            ``True`` when the subscriber accepted the event, ``False`` when
            every attempt raised.
        """
        callback_name = getattr(callback, "__name__", repr(callback))

        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                callback(event)
                return True
            except Exception as exc:  # noqa: BLE001 - a bad subscriber must not break dispatch
                error_type = type(exc).__name__
                if attempt >= self._retry.max_attempts:
                    logger.warning(
                        "Subscriber failed after all retry attempts",
                        extra={
                            "topic": topic,
                            "callback": callback_name,
                            "error_type": error_type,
                            "attempts": attempt,
                        },
                    )
                    return False

                delay = self._retry.delay_for_attempt(attempt)
                logger.warning(
                    "Subscriber raised an exception, retrying with backoff",
                    extra={
                        "topic": topic,
                        "callback": callback_name,
                        "error_type": error_type,
                        "attempt": attempt,
                        "max_attempts": self._retry.max_attempts,
                        "retry_delay_seconds": delay,
                    },
                )
                self._sleep(delay)

        return False  # pragma: no cover - loop always returns

    # ------------------------------------------------------------------
    # Paginated history
    # ------------------------------------------------------------------

    def list_events(
        self,
        topic: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Return a paginated slice of the event history.

        Args:
            topic: When supplied, only events whose ``"topic"`` matches are
                returned.  Pass ``None`` (default) to include all events.
            page: 1-based page number.  Values below 1 are clamped to 1.
            page_size: Number of items per page.  Values below 1 are clamped
                to 1.

        Returns:
            A dict with the following keys:

            * ``items``     – list of event dicts for the requested page.
            * ``total``     – total number of matching events.
            * ``page``      – the current (1-based) page number.
            * ``page_size`` – the effective page size used.
            * ``pages``     – total number of pages.
        """
        page = max(1, page)
        page_size = max(1, page_size)

        if topic is not None:
            filtered = [e for e in self._events if e.get("topic") == topic]
        else:
            filtered = list(self._events)

        total = len(filtered)
        pages = max(1, -(-total // page_size))  # ceiling division

        offset = (page - 1) * page_size
        items = filtered[offset : offset + page_size]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return a health snapshot of the event listener.

        The snapshot is intentionally cheap: it only reads in-memory state and
        never touches Redis, the database, or the network, so it is safe to
        call from a liveness/readiness probe.

        Returns:
            A dict with the following keys:

            * ``status``       – always ``"ok"``: the bus is in-process, so a
              reachable service is a healthy one.
            * ``total_events`` – number of events currently held in history.
            * ``topic_count``  – number of distinct topics seen in history.
        """
        topic_count = len({event.get("topic") for event in self._events})
        return {
            "status": "ok",
            "total_events": len(self._events),
            "topic_count": topic_count,
        }


# Module-level singleton used by the endpoint router.
event_listener = EventListener()
