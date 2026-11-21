"""Workspace-local FastAPI router for assembling operational timelines.

The router is deliberately not included in the core application. It exposes
explicit Pydantic request/response models and pure helpers so maintainers can
wire it later without changing routed behavior elsewhere in the repository.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TIMELINE_EVENTS = 1000
DEFAULT_MAX_EVENTS = 100
MAX_MERGE_BATCHES = 20

router = APIRouter(prefix="/timeline", tags=["SLA Operations Timeline"])


class TimelineEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., min_length=1, max_length=200)
    occurred_at: datetime
    domain: str = Field(..., min_length=1, max_length=64)
    kind: str = Field(..., min_length=1, max_length=64)
    summary: str = Field(default="", max_length=1000)
    reference: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id", "domain", "kind")
    @classmethod
    def _trim_required_text(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("must not be blank")
        return trimmed

    @field_validator("occurred_at")
    @classmethod
    def _make_timezone_aware(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class TimelineAssemblyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[TimelineEventIn] = Field(default_factory=list, max_length=MAX_TIMELINE_EVENTS)
    domains: list[str] = Field(default_factory=list, max_length=50)
    kinds: list[str] = Field(default_factory=list, max_length=50)
    order: Literal["asc", "desc"] = "asc"
    max_events: int = Field(default=DEFAULT_MAX_EVENTS, ge=1, le=MAX_TIMELINE_EVENTS)

    @field_validator("domains", "kinds")
    @classmethod
    def _trim_filters(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class TimelineEventOut(BaseModel):
    sequence: int = Field(..., ge=1)
    event_id: str
    occurred_at: datetime
    domain: str
    kind: str
    summary: str
    reference: str | None
    metadata: dict[str, Any]


class TimelineAssemblyResponse(BaseModel):
    items: list[TimelineEventOut]
    total_input: int = Field(..., ge=0)
    total_matched: int = Field(..., ge=0)
    returned: int = Field(..., ge=0)
    truncated: bool
    window_start: datetime | None
    window_end: datetime | None


class TimelineMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timelines: list[list[TimelineEventIn]] = Field(..., min_length=1, max_length=MAX_MERGE_BATCHES)
    order: Literal["asc", "desc"] = "asc"
    max_events: int = Field(default=DEFAULT_MAX_EVENTS, ge=1, le=MAX_TIMELINE_EVENTS)


def assemble_timeline(payload: TimelineAssemblyRequest | Mapping[str, Any]) -> TimelineAssemblyResponse:
    """Validate, filter, de-duplicate, order, and cap timeline events."""
    request = payload if isinstance(payload, TimelineAssemblyRequest) else TimelineAssemblyRequest.model_validate(payload)
    domains = {value.casefold() for value in request.domains}
    kinds = {value.casefold() for value in request.kinds}

    selected = [
        event
        for event in request.events
        if (not domains or event.domain.casefold() in domains) and (not kinds or event.kind.casefold() in kinds)
    ]
    selected.sort(key=lambda event: (event.occurred_at, event.event_id))

    deduplicated: list[TimelineEventIn] = []
    seen: set[str] = set()
    for event in selected:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        deduplicated.append(event)

    if request.order == "desc":
        deduplicated.reverse()

    visible = deduplicated[: request.max_events]
    items = [
        TimelineEventOut(sequence=sequence, **event.model_dump())
        for sequence, event in enumerate(visible, start=1)
    ]
    return TimelineAssemblyResponse(
        items=items,
        total_input=len(request.events),
        total_matched=len(deduplicated),
        returned=len(items),
        truncated=len(visible) < len(deduplicated),
        window_start=visible[0].occurred_at if visible else None,
        window_end=visible[-1].occurred_at if visible else None,
    )


def merge_timeline_batches(payload: TimelineMergeRequest | Mapping[str, Any]) -> TimelineAssemblyResponse:
    """Flatten independent event batches into one assembled timeline."""
    request = payload if isinstance(payload, TimelineMergeRequest) else TimelineMergeRequest.model_validate(payload)
    events = [event for batch in request.timelines for event in batch]
    return assemble_timeline(
        TimelineAssemblyRequest(events=events, order=request.order, max_events=request.max_events)
    )


@router.post("/assemble", response_model=TimelineAssemblyResponse, summary="Assemble a validated timeline")
def assemble_timeline_endpoint(payload: TimelineAssemblyRequest) -> TimelineAssemblyResponse:
    return assemble_timeline(payload)


@router.post("/merge", response_model=TimelineAssemblyResponse, summary="Merge timeline event batches")
def merge_timeline_batches_endpoint(payload: TimelineMergeRequest) -> TimelineAssemblyResponse:
    return merge_timeline_batches(payload)
