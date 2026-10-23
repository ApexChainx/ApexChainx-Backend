"""Events API router.

Exposes the health check for the in-process :class:`~app.services.event_listener.EventListener`.

Routes
------
``GET /api/v1/events/health`` — EventListener health snapshot.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.services.event_listener import EventListener, event_listener

router = APIRouter(prefix="/events", tags=["events"])


def get_event_listener() -> EventListener:
    """Return the process-wide :class:`EventListener` singleton.

    Exposed as a FastAPI dependency so tests can swap in an isolated listener
    with ``app.dependency_overrides``.

    Returns:
        The module-level :data:`app.services.event_listener.event_listener`
        instance.
    """
    return event_listener


class EventListenerHealth(BaseModel):
    """Health snapshot returned by ``GET /api/v1/events/health``.

    Attributes:
        status: Always ``"ok"`` when the event bus is serving requests.
        total_events: Number of events currently held in the in-memory history.
        topic_count: Number of distinct topics seen in the event history.

    Example:
        .. code-block:: json

            {"status": "ok", "total_events": 42, "topic_count": 3}
    """

    status: Literal["ok"] = Field(
        ...,
        description="Service status.  Always 'ok' when the event bus is reachable.",
        examples=["ok"],
    )
    total_events: int = Field(
        ...,
        ge=0,
        description="Number of events currently held in the in-memory history.",
        examples=[42],
    )
    topic_count: int = Field(
        ...,
        ge=0,
        description="Number of distinct topics seen in the event history.",
        examples=[3],
    )


@router.get(
    "/health",
    response_model=EventListenerHealth,
    status_code=status.HTTP_200_OK,
    summary="EventListener health check",
    description=(
        "Report the health of the in-process EventListener service.  "
        "The snapshot is generated from memory only — no database, Redis, or "
        "network call is made — so it is suitable for liveness probes."
    ),
    response_description="EventListener health snapshot",
    responses={
        status.HTTP_200_OK: {
            "description": "The event bus is healthy.",
            "content": {"application/json": {"example": {"status": "ok", "total_events": 42, "topic_count": 3}}},
        }
    },
)
def get_event_listener_health(
    listener: Annotated[EventListener, Depends(get_event_listener)],
) -> EventListenerHealth:
    """Return a health snapshot for the EventListener service.

    Args:
        listener: The :class:`~app.services.event_listener.EventListener`
            injected by :func:`get_event_listener`.

    Returns:
        An :class:`EventListenerHealth` describing the current state of the
        event bus.
    """
    return EventListenerHealth(**listener.health())
