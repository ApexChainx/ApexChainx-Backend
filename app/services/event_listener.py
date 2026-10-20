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

The HTTP surface for this service lives in
``app/api/v1/endpoints/event_listener.py``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Topic that receives every event, regardless of the event's own topic.
WILDCARD_TOPIC = "*"


class EventListener:
    """In-process event bus with paginated history retrieval.

    The listener keeps two pieces of state:

    * ``_events`` — every event that has been processed, in insertion order.
    * ``_subscribers`` — a mapping of topic to the callables registered for it.

    Instances are cheap to create, which makes it easy to build an isolated
    listener per request or per test.  The application uses the module-level
    :data:`event_listener` singleton.
    """

    def __init__(self) -> None:
        # All events, in insertion order.
        self._events: list[dict[str, Any]] = []
        # Topic → list of subscriber callables.  "*" catches every topic.
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)

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
                :meth:`process_event`; exceptions it raises are logged and
                do not prevent delivery to the remaining subscribers.
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
        for callback in recipients:
            self._notify(callback, stored, topic)

        logger.debug(
            "Event processed",
            extra={"topic": topic, "subscribers_notified": len(recipients)},
        )
        return stored

    def _recipients(self, topic: str) -> list[Callable[[dict[str, Any]], None]]:
        """Return the subscribers for *topic*, wildcard subscribers last."""
        return list(self._subscribers.get(topic, [])) + list(self._subscribers.get(WILDCARD_TOPIC, []))

    @staticmethod
    def _notify(
        callback: Callable[[dict[str, Any]], None],
        event: dict[str, Any],
        topic: str,
    ) -> None:
        """Invoke *callback* with *event*, logging (not raising) failures."""
        try:
            callback(event)
        except Exception as exc:  # noqa: BLE001 - a bad subscriber must not break dispatch
            logger.warning(
                "Subscriber raised an exception",
                extra={
                    "topic": topic,
                    "error_type": type(exc).__name__,
                    "callback": getattr(callback, "__name__", repr(callback)),
                },
            )

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
