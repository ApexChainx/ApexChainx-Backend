"""Normalize and order operational events into per-outage timelines (#868).

Input events are plain mappings, which keeps this service independent of ORM
models and database sessions. Assumptions:

* ``occurred_at`` (or ``timestamp``) accepts an ISO-8601 string, a datetime, or
  epoch seconds; naive datetimes are interpreted as UTC. Events without a
  parseable timestamp are skipped with a structured reason.
* ``event_id``/``id`` and ``outage_id``/``outage`` are alternative keys; missing
  ids fall back to a deterministic synthetic id and an ``unassigned`` bucket.
* Event type labels are trimmed and lower-cased; ``detail`` is copied shallowly
  and keeps string keys only so the output stays JSON-friendly.
* Timelines are keyed by outage id in sorted order. Ascending/descending sorts
  compare timestamps only, so equal timestamps retain input order (stable sort).

Complexity: O(n log n) for n events.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

UNASSIGNED = "unassigned"
UNKNOWN_EVENT_TYPE = "unknown"


class SkippedEvent(BaseModel):
    """An input entry that could not be normalized into the timeline."""

    source_index: int
    reason: str
    event_id: str | None = None


class TimelineEvent(BaseModel):
    """One normalized operational event."""

    source_index: int
    event_id: str
    outage_id: str
    event_type: str
    occurred_at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


class Timeline(BaseModel):
    """Ordered events for a single outage."""

    outage_id: str
    events: list[TimelineEvent] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    event_count: int = 0


class TimelineReport(BaseModel):
    """Result of assembling all events into timelines."""

    total_events: int
    normalized_events: int
    timelines: list[Timeline] = Field(default_factory=list)
    skipped: list[SkippedEvent] = Field(default_factory=list)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _detail(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _events(events: Iterable[object] | None) -> Iterable[object]:
    if events is None:
        return ()
    try:
        return iter(events)
    except TypeError:
        return ()


def assemble_timelines(
    events: Iterable[object] | None,
    *,
    order: Literal["asc", "desc"] = "asc",
) -> TimelineReport:
    """Normalize events and group them into deterministic outage timelines.

    Args:
        events: Iterable of event-like mappings. ``None`` is treated as empty.
        order: ``asc`` (default) or ``desc``; any other value is treated as asc.

    Returns:
        A report with timelines ordered by outage id, events ordered by
        timestamp, and skipped entries for unusable input.
    """
    reverse = order == "desc"
    normalized: list[TimelineEvent] = []
    skipped: list[SkippedEvent] = []
    total = 0

    for index, raw_event in enumerate(_events(events)):
        total += 1
        if not isinstance(raw_event, Mapping):
            skipped.append(
                SkippedEvent(
                    source_index=index,
                    reason=f"expected a mapping, got {type(raw_event).__name__}",
                )
            )
            continue

        event_id = _text(raw_event.get("event_id")) or _text(raw_event.get("id"))
        occurred_at = _parse_timestamp(raw_event.get("occurred_at", raw_event.get("timestamp")))
        if occurred_at is None:
            skipped.append(
                SkippedEvent(
                    source_index=index,
                    reason="occurred_at is missing or not a supported timestamp",
                    event_id=event_id,
                )
            )
            continue

        outage_id = _text(raw_event.get("outage_id")) or _text(raw_event.get("outage")) or UNASSIGNED
        event_type = (
            _text(raw_event.get("event_type")) or _text(raw_event.get("type")) or UNKNOWN_EVENT_TYPE
        ).lower()
        normalized.append(
            TimelineEvent(
                source_index=index,
                event_id=event_id or f"event-{index}",
                outage_id=outage_id,
                event_type=event_type,
                occurred_at=occurred_at,
                detail=_detail(raw_event.get("detail")),
            )
        )

    grouped: dict[str, list[TimelineEvent]] = {}
    for event in normalized:
        grouped.setdefault(event.outage_id, []).append(event)

    timelines: list[Timeline] = []
    for outage_id in sorted(grouped):
        items = sorted(grouped[outage_id], key=lambda event: event.occurred_at, reverse=reverse)
        timelines.append(
            Timeline(
                outage_id=outage_id,
                events=items,
                started_at=min(event.occurred_at for event in items),
                ended_at=max(event.occurred_at for event in items),
                event_count=len(items),
            )
        )

    return TimelineReport(
        total_events=total,
        normalized_events=len(normalized),
        timelines=timelines,
        skipped=skipped,
    )
