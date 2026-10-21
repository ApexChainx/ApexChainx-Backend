"""Typed errors raised by the event listener (issue #736).

The listener sits on the fan-out path: a single corrupt registration row, an
unreachable cache or one subscriber that was deleted underneath us must not be
able to turn a routine SLA event into an opaque ``500``.

Every recoverable failure therefore has its own type here, so a caller can
tell "this subscriber is broken" from "the database is gone" from "the event
itself is not deliverable" and react to each one differently.
"""

from __future__ import annotations


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


class BackendUnavailableError(EventListenerError):
    """A Redis-backed helper (cache or rate limiter) was unreachable.

    The listener degrades to its in-process behaviour instead of failing the
    event, so this is *reported* rather than raised at the call site.
    """

    error_code = "event_listener_backend_unavailable"


class SubscriberRateLimitedError(EventListenerError):
    """A subscriber used up the number of events it may receive per window."""

    error_code = "event_listener_rate_limited"

    def __init__(
        self,
        detail: str,
        *,
        event: str | None = None,
        subscriber_id: str | None = None,
        retry_after_seconds: int = 0,
    ) -> None:
        super().__init__(detail, event=event)
        self.subscriber_id = subscriber_id
        self.retry_after_seconds = retry_after_seconds
