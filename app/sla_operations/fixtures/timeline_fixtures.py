"""Deterministic operational event sequences for timeline assembly tests.

Events use the workspace's outage-event response shape and fixed, timezone-aware
timestamps. Each helper call creates fresh records and nested detail mappings.
"""

from datetime import UTC, datetime
from typing import Literal, TypedDict


class TimelineEvent(TypedDict):
    id: str
    outage_id: str
    event_type: Literal["created", "updated", "resolved", "sla_computed", "sla_recomputed"]
    schema_version: str
    detail: dict[str, str | int]
    occurred_at: datetime


def timeline_event_fixtures() -> list[TimelineEvent]:
    """Return ordered lifecycle events for two outages, including a tie timestamp."""
    return [
        {
            "id": "event-001",
            "outage_id": "outage-001",
            "event_type": "created",
            "schema_version": "1",
            "detail": {"site_name": "North Ridge Relay"},
            "occurred_at": datetime(2026, 11, 18, 8, 0, tzinfo=UTC),
        },
        {
            "id": "event-002",
            "outage_id": "outage-001",
            "event_type": "updated",
            "schema_version": "1",
            "detail": {"changes": "assigned_to:ops-on-call"},
            "occurred_at": datetime(2026, 11, 18, 8, 5, tzinfo=UTC),
        },
        {
            "id": "event-003",
            "outage_id": "outage-001",
            "event_type": "sla_computed",
            "schema_version": "1",
            "detail": {"status": "violated"},
            "occurred_at": datetime(2026, 11, 18, 8, 42, tzinfo=UTC),
        },
        {
            "id": "event-004",
            "outage_id": "outage-001",
            "event_type": "resolved",
            "schema_version": "1",
            "detail": {"mttr_minutes": 42},
            "occurred_at": datetime(2026, 11, 18, 8, 42, tzinfo=UTC),
        },
        {
            "id": "event-005",
            "outage_id": "outage-001",
            "event_type": "sla_recomputed",
            "schema_version": "1",
            "detail": {"status": "violated"},
            "occurred_at": datetime(2026, 11, 18, 8, 45, tzinfo=UTC),
        },
        {
            "id": "event-006",
            "outage_id": "outage-002",
            "event_type": "created",
            "schema_version": "1",
            "detail": {"site_name": "Harbor Exchange"},
            "occurred_at": datetime(2026, 11, 18, 9, 15, tzinfo=UTC),
        },
    ]
