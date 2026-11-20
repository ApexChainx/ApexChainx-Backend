"""Unit tests for normalized timeline event schemas."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.timeline_events import (
    GroupedTimeline,
    TimelineEvent,
    TimelineEventCreate,
    TimelineEventGroup,
    TimelineEventType,
    TimelineGrouping,
)


def _event_payload(**overrides):
    payload = {
        "id": "evt_1",
        "outage_id": "outage-1",
        "event_type": "sla_computed",
        "occurred_at": "2026-01-01T12:00:00+02:00",
        "actor": "sla-worker",
        "detail": {"status": "met"},
        "source": "sla-service",
    }
    payload.update(overrides)
    return payload


def test_create_event_normalizes_occurred_at_and_uses_defaults():
    event = TimelineEventCreate(
        outage_id="outage-1",
        event_type=TimelineEventType.resolved,
        occurred_at="2026-01-01T12:00:00+02:00",
    )
    assert event.occurred_at == datetime(2026, 1, 1, 10, tzinfo=UTC)
    assert event.actor is None
    assert event.detail is None
    assert TimelineEventCreate(outage_id="outage-1", event_type="detected").occurred_at.tzinfo is UTC


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_type": "unknown_event"},
        {"outage_id": ""},
        {"occurred_at": "2026-01-01T00:00:00"},
        {"actor": ""},
        {"detail": ["not", "a", "mapping"]},
    ],
)
def test_create_event_rejects_invalid_payloads(overrides):
    payload = {"outage_id": "outage-1", "event_type": "detected"}
    payload.update(overrides)
    with pytest.raises(ValidationError):
        TimelineEventCreate(**payload)


def test_stored_event_has_schema_version_and_reads_attributes():
    event = TimelineEvent(**_event_payload())
    assert event.schema_version == "1.0"
    raw = SimpleNamespace(**{key: value for key, value in _event_payload().items() if key != "id"})
    raw.id = "evt_2"
    assert TimelineEvent.model_validate(raw).event_type is TimelineEventType.sla_computed
    with pytest.raises(ValidationError):
        TimelineEvent(**_event_payload(schema_version=""))


def test_grouped_timeline_reconciles_totals_and_group_keys():
    group = TimelineEventGroup(
        key="outage-1",
        group_by=TimelineGrouping.outage,
        total=2,
        events=[TimelineEvent(**_event_payload())],
    )
    timeline = GroupedTimeline(groups=[group], total_events=2, outage_id="outage-1")
    assert timeline.groups[0].total == 2
    assert timeline.total_events == 2

    with pytest.raises(ValidationError, match="cannot contain more events than its total"):
        TimelineEventGroup(
            key="day", group_by=TimelineGrouping.day, total=0, events=[TimelineEvent(**_event_payload())]
        )
    with pytest.raises(ValidationError, match="total_events must equal"):
        GroupedTimeline(groups=[group], total_events=3)
    with pytest.raises(ValidationError, match="group keys must be unique"):
        GroupedTimeline(groups=[group, group], total_events=4)


def test_grouped_timeline_rejects_unknown_grouping_and_negative_total():
    with pytest.raises(ValidationError):
        TimelineEventGroup(key="x", group_by="week", total=1)
    with pytest.raises(ValidationError):
        GroupedTimeline(groups=[], total_events=-1)
