"""
EventListener service.

Provides an in-process, topic-based event bus with paginated history retrieval.
Events are stored in an ordered list; subscribers are callable hooks registered
per topic (or the wildcard ``"*"`` to receive every event).

Usage example::

    listener = EventListener()
    listener.subscribe("outage.created", my_callback)

    listener.process_event({"topic": "outage.created", "payload": {...}})

    page = listener.list_events(topic="outage.created", page=1, page_size=20)
    # {"items": [...], "total": 1, "page": 1, "page_size": 20, "pages": 1}
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventListener:
    """In-process event bus with paginated history retrieval."""

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
            topic: The event topic to subscribe to.  Pass ``"*"`` to receive
                every event regardless of topic.
            callback: A callable that accepts the event dict as its sole argument.
        """
        self._subscribers[topic].append(callback)
        logger.debug(
            "Subscriber registered",
            extra={"topic": topic, "callback": getattr(callback, "__name__", repr(callback))},
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
            ``True`` if the callback was found and removed, ``False`` otherwise.
        """
        subscribers = self._subscribers.get(topic, [])
        try:
            subscribers.remove(callback)
            logger.debug(
                "Subscriber removed",
                extra={"topic": topic, "callback": getattr(callback, "__name__", repr(callback))},
            )
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Event processing
    # ------------------------------------------------------------------

    def process_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Record *event* and dispatch it to all matching subscribers.

        The event dict is augmented with a ``received_at`` ISO-8601 timestamp
        if one is not already present.

        Args:
            event: A mapping that should include at minimum a ``"topic"`` key.

        Returns:
            The stored event dict (with the ``received_at`` field set).
        """
        topic = event.get("topic", "unknown")
        stored = {
            **event,
            "received_at": event.get(
                "received_at",
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        }
        self._events.append(stored)

        # Dispatch to exact-topic subscribers, then wildcard subscribers.
        recipients = self._subscribers.get(topic, []) + self._subscribers.get("*", [])
        for callback in recipients:
            try:
                callback(stored)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Subscriber raised an exception",
                    extra={
                        "topic": topic,
                        "error_type": type(exc).__name__,
                        "callback": getattr(callback, "__name__", repr(callback)),
                    },
                )

        logger.debug(
            "Event processed",
            extra={"topic": topic, "subscribers_notified": len(recipients)},
        )
        return stored

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
        """Return a simple health snapshot of the event listener.

        Returns:
            A dict with ``status``, ``total_events``, and ``topic_count``.
        """
        return {
            "status": "ok",
            "total_events": len(self._events),
            "topic_count": len(self._subscribers),
        }


# Module-level singleton used by the endpoint router.
event_listener = EventListener()
