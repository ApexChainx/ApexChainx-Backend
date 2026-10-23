"""Prometheus metrics for the event listener (issue #738).

The listener sits on the path every domain event takes, so the questions an
operator asks about it -- how many events are emitted, how many subscribers
hear about them, how long the resolution takes, how often it fails -- have to
be answerable from the metrics endpoint and not from log archaeology.

Every series is tagged with the domain event so a single event type can be
watched on its own.
"""

from __future__ import annotations

from app.services.metrics import increment_counter, record_histogram, set_gauge

# Number of events the listener resolved the subscribers for.
EVENT_LISTENER_RESOLUTION_TOTAL = "event_listener_resolution_total"
# How long resolving the subscribers took, in seconds.
EVENT_LISTENER_RESOLUTION_DURATION = "event_listener_resolution_duration_seconds"
# Number of subscribers a resolution returned.
EVENT_LISTENER_SUBSCRIBERS = "event_listener_subscribers"
# Number of deliveries the listener handed to the delivery pipeline.
EVENT_LISTENER_DISPATCH_TOTAL = "event_listener_dispatch_total"
# Number of subscribers that were skipped while dispatching.
EVENT_LISTENER_SUBSCRIBER_SKIPPED_TOTAL = "event_listener_subscriber_skipped_total"
# Number of failures the listener ran into.
EVENT_LISTENER_ERROR_TOTAL = "event_listener_error_total"


class EventListenerMetrics:
    """The Prometheus series the event listener publishes."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def resolution_started(self, event: str) -> None:
        """Count a resolution of the subscribers of ``event``."""
        if not self._enabled:
            return
        increment_counter(EVENT_LISTENER_RESOLUTION_TOTAL, tags={"event": event})

    def resolution_completed(self, event: str, subscriber_count: int, duration_seconds: float) -> None:
        """Record the outcome and the duration of a resolution."""
        if not self._enabled:
            return
        set_gauge(EVENT_LISTENER_SUBSCRIBERS, float(subscriber_count), tags={"event": event})
        record_histogram(
            EVENT_LISTENER_RESOLUTION_DURATION,
            duration_seconds,
            tags={"event": event},
        )

    def dispatch_completed(self, event: str, delivery_count: int) -> None:
        """Count the deliveries the listener handed over for ``event``."""
        if not self._enabled:
            return
        increment_counter(EVENT_LISTENER_DISPATCH_TOTAL, value=float(delivery_count), tags={"event": event})

    def subscriber_skipped(self, event: str, reason: str) -> None:
        """Count a subscriber that did not receive the event."""
        if not self._enabled:
            return
        increment_counter(
            EVENT_LISTENER_SUBSCRIBER_SKIPPED_TOTAL,
            tags={"event": event, "reason": reason},
        )

    def error(self, event: str, stage: str) -> None:
        """Count a failure in one of the listener's stages."""
        if not self._enabled:
            return
        increment_counter(EVENT_LISTENER_ERROR_TOTAL, tags={"event": event, "stage": stage})
