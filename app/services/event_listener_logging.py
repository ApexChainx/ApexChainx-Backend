"""Structured logging for the event listener (issue #737).

The listener makes a small number of decisions -- resolve the subscribers,
apply the fan-out policy, hand the deliveries over -- and each of those
decisions has to be greppable. A log line whose meaning lives in free text
cannot be queried, so the names of the lines live here as constants and the
helper below is the only way the listener logs.

Every line carries the same fields:

``event_name``
    The stable name of the line. This is the field to filter on.
``event``
    The domain event the line is about (``sla.violation`` and friends).
``subscriber_id``
    Present when the line is about one subscriber.

The correlation id is added by :class:`app.utils.logging.StructuredLogger`,
so a line can always be tied back to the request or task that caused it.
"""

from __future__ import annotations

from typing import Any

from app.utils.logging import StructuredLogger, get_structured_logger

RESOLVE_STARTED = "event_listener.resolve_started"
RESOLVE_COMPLETED = "event_listener.resolve_completed"
RESOLVE_FAILED = "event_listener.resolve_failed"
DISPATCH_COMPLETED = "event_listener.dispatch_completed"
SUBSCRIBER_SKIPPED = "event_listener.subscriber_skipped"


class EventListenerLogger:
    """Structured logger for the event listener."""

    def __init__(self, name: str) -> None:
        self._logger: StructuredLogger = get_structured_logger(name)

    def resolve_started(self, event: str, **fields: Any) -> None:
        """The listener started resolving the subscribers of ``event``."""
        self._logger.info(RESOLVE_STARTED, event_name=RESOLVE_STARTED, event=event, **fields)

    def resolve_completed(self, event: str, subscriber_count: int, duration_ms: float, **fields: Any) -> None:
        """The listener resolved ``subscriber_count`` subscribers for ``event``."""
        self._logger.info(
            RESOLVE_COMPLETED,
            event_name=RESOLVE_COMPLETED,
            event=event,
            subscriber_count=subscriber_count,
            duration_ms=round(duration_ms, 2),
            **fields,
        )

    def resolve_failed(self, event: str, error: str, **fields: Any) -> None:
        """The listener could not resolve the subscribers of ``event``."""
        self._logger.error(
            RESOLVE_FAILED,
            event_name=RESOLVE_FAILED,
            event=event,
            error=error,
            **fields,
        )

    def dispatch_completed(self, event: str, delivery_count: int, **fields: Any) -> None:
        """The listener handed ``delivery_count`` deliveries over for ``event``."""
        self._logger.info(
            DISPATCH_COMPLETED,
            event_name=DISPATCH_COMPLETED,
            event=event,
            delivery_count=delivery_count,
            **fields,
        )

    def subscriber_skipped(self, event: str, subscriber_id: str, reason: str, **fields: Any) -> None:
        """A subscriber did not receive ``event``."""
        self._logger.warning(
            SUBSCRIBER_SKIPPED,
            event_name=SUBSCRIBER_SKIPPED,
            event=event,
            subscriber_id=subscriber_id,
            reason=reason,
            **fields,
        )
