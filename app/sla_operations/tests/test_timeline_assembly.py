from datetime import UTC, datetime, timedelta

import pytest

from app.sla_operations.services.timeline_assembly import assemble_timelines

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
T2 = T0 + timedelta(minutes=10)


def test_events_are_grouped_and_ordered_by_outage():
    events = [
        {"event_id": "e2", "outage_id": "outage-1", "event_type": "RESOLVED", "occurred_at": T2, "detail": {"mttr_minutes": 10}},
        {"event_id": "e1", "outage_id": "outage-1", "event_type": "created", "occurred_at": T0, "detail": {"site_name": "Site A"}},
        {"event_id": "e3", "outage_id": "outage-2", "event_type": "created", "occurred_at": "2026-01-01T00:07:00Z"},
    ]

    report = assemble_timelines(events)

    assert (report.total_events, report.normalized_events) == (3, 3)
    assert report.skipped == []
    assert [timeline.outage_id for timeline in report.timelines] == ["outage-1", "outage-2"]
    first = report.timelines[0]
    assert first.event_count == 2
    assert [event.event_id for event in first.events] == ["e1", "e2"]
    assert first.started_at == T0
    assert first.ended_at == T2
    assert first.events[1].event_type == "resolved"
    assert first.events[1].detail == {"mttr_minutes": 10}


def test_descending_order_keeps_input_order_for_ties():
    events = [
        {"event_id": "a", "outage_id": "outage-1", "occurred_at": T0},
        {"event_id": "b", "outage_id": "outage-1", "occurred_at": T0},
        {"event_id": "c", "outage_id": "outage-1", "occurred_at": T1},
    ]

    report = assemble_timelines(events, order="desc")

    assert [event.event_id for event in report.timelines[0].events] == ["c", "a", "b"]
    assert report.timelines[0].started_at == T0
    assert report.timelines[0].ended_at == T1


def test_missing_optional_fields_use_deterministic_fallbacks():
    report = assemble_timelines([{"occurred_at": T0}])

    event = report.timelines[0].events[0]
    assert report.timelines[0].outage_id == "unassigned"
    assert event.event_id == "event-0"
    assert event.event_type == "unknown"
    assert event.detail == {}


def test_unusable_events_are_skipped_with_reasons():
    events = [
        42,
        {"event_id": "x", "outage_id": "outage-1", "occurred_at": "not-a-date"},
        {"event_id": "y", "outage_id": "outage-1", "occurred_at": "2026-01-01T00:00:00"},
    ]

    report = assemble_timelines(events)

    assert (report.total_events, report.normalized_events) == (3, 1)
    assert [item.source_index for item in report.skipped] == [0, 1]
    assert report.skipped[1].event_id == "x"
    assert report.timelines[0].events[0].occurred_at == T0


@pytest.mark.parametrize("events", [None, [], 42])
def test_empty_and_non_iterable_inputs_are_safe(events):
    report = assemble_timelines(events)

    assert report.total_events == 0
    assert report.normalized_events == 0
    assert report.timelines == []
    assert report.skipped == []


def test_epoch_timestamp_and_alternative_keys_are_normalized():
    report = assemble_timelines(
        [{"id": "e", "outage": "outage-9", "type": " PATCHED ", "timestamp": 0}]
    )

    timeline = report.timelines[0]
    assert timeline.outage_id == "outage-9"
    assert timeline.events[0].event_id == "e"
    assert timeline.events[0].event_type == "patched"
    assert timeline.events[0].occurred_at == datetime.fromtimestamp(0, UTC)


def test_detail_is_copied_without_mutating_input():
    detail = {"site_name": "Site A", 1: "ignored"}
    events = [{"event_id": "e", "outage_id": "o", "occurred_at": T0, "detail": detail}]

    report = assemble_timelines(events)

    assert report.timelines[0].events[0].detail == {"site_name": "Site A"}
    assert detail == {"site_name": "Site A", 1: "ignored"}
