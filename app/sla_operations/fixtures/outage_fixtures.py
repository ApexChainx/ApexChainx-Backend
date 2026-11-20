"""Deterministic outage samples spanning severities, statuses, and MTTR cases.

Use ``get_outage_fixtures`` to receive deep copies safe for consumer mutation.
"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal, TypedDict


class SLAStatusFixture(TypedDict):
    status: Literal["in_progress", "met", "violated"]
    mttr_minutes: int | None
    threshold_minutes: int
    time_remaining_minutes: int | None


class LocationFixture(TypedDict):
    latitude: float
    longitude: float


class OutageFixture(TypedDict):
    id: str
    site_name: str
    site_id: str
    severity: Literal["critical", "high", "medium", "low"]
    status: Literal["active", "resolved", "investigating"]
    detected_at: datetime
    resolved_at: datetime | None
    description: str
    affected_services: list[str]
    affected_subscribers: int
    assigned_to: str
    created_by: str
    location: LocationFixture
    sla_status: SLAStatusFixture


_OUTAGES: tuple[OutageFixture, ...] = (
    {
        "id": "outage-critical-active-001",
        "site_name": "North Harbor Exchange",
        "site_id": "site-nh-01",
        "severity": "critical",
        "status": "active",
        "detected_at": datetime(2025, 1, 15, 8, 30, tzinfo=UTC),
        "resolved_at": None,
        "description": "Backhaul fiber interruption affecting the exchange.",
        "affected_services": ["5G", "voice", "fixed_broadband"],
        "affected_subscribers": 12400,
        "assigned_to": "network-response",
        "created_by": "noc",
        "location": {"latitude": 37.7749, "longitude": -122.4194},
        "sla_status": {
            "status": "in_progress",
            "mttr_minutes": None,
            "threshold_minutes": 60,
            "time_remaining_minutes": 22,
        },
    },
    {
        "id": "outage-high-resolved-002",
        "site_name": "Cedar Ridge Tower",
        "site_id": "site-cr-02",
        "severity": "high",
        "status": "resolved",
        "detected_at": datetime(2025, 1, 12, 14, 0, tzinfo=UTC),
        "resolved_at": datetime(2025, 1, 12, 14, 42, tzinfo=UTC),
        "description": "Power controller failure recovered after failover.",
        "affected_services": ["4G", "voice"],
        "affected_subscribers": 2850,
        "assigned_to": "field-team-west",
        "created_by": "monitoring",
        "location": {"latitude": 39.7392, "longitude": -104.9903},
        "sla_status": {
            "status": "met",
            "mttr_minutes": 42,
            "threshold_minutes": 90,
            "time_remaining_minutes": None,
        },
    },
    {
        "id": "outage-medium-breach-003",
        "site_name": "Riverbend Data Center",
        "site_id": "site-rd-03",
        "severity": "medium",
        "status": "resolved",
        "detected_at": datetime(2025, 1, 10, 9, 15, tzinfo=UTC),
        "resolved_at": datetime(2025, 1, 10, 10, 45, tzinfo=UTC),
        "description": "Cooling maintenance caused a temporary service degradation.",
        "affected_services": ["core_network"],
        "affected_subscribers": 940,
        "assigned_to": "facilities-ops",
        "created_by": "noc",
        "location": {"latitude": 41.8781, "longitude": -87.6298},
        "sla_status": {
            "status": "violated",
            "mttr_minutes": 90,
            "threshold_minutes": 60,
            "time_remaining_minutes": None,
        },
    },
    {
        "id": "outage-low-immediate-004",
        "site_name": "Lakeside Relay",
        "site_id": "site-lr-04",
        "severity": "low",
        "status": "resolved",
        "detected_at": datetime(2025, 1, 8, 11, 0, tzinfo=UTC),
        "resolved_at": datetime(2025, 1, 8, 11, 0, tzinfo=UTC),
        "description": "Transient relay alert cleared without subscriber impact.",
        "affected_services": ["telemetry"],
        "affected_subscribers": 0,
        "assigned_to": "monitoring",
        "created_by": "monitoring",
        "location": {"latitude": 44.9778, "longitude": -93.2650},
        "sla_status": {
            "status": "met",
            "mttr_minutes": 0,
            "threshold_minutes": 240,
            "time_remaining_minutes": None,
        },
    },
)


def get_outage_fixtures() -> list[OutageFixture]:
    """Return fresh outage records with aware UTC timestamps and realistic edge cases."""
    return deepcopy(list(_OUTAGES))
