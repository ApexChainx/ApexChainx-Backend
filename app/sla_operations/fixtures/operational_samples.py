"""Stable, isolated sample outage summaries for SLA operations."""

from copy import deepcopy
from typing import Literal, TypedDict


class OutageSummary(TypedDict):
    outage_id: str
    severity: Literal["critical", "high", "medium", "low"]
    status: Literal["open", "resolved"]
    started_at: str
    resolved_at: str | None
    mttr_minutes: int | None
    threshold_minutes: int
    affected_services: list[str]


_SAMPLE_OUTAGE_SUMMARIES: tuple[OutageSummary, ...] = (
    {
        "outage_id": "sample-outage-met",
        "severity": "high",
        "status": "resolved",
        "started_at": "2026-01-15T10:00:00+00:00",
        "resolved_at": "2026-01-15T10:30:00+00:00",
        "mttr_minutes": 30,
        "threshold_minutes": 60,
        "affected_services": ["mobile-data", "voice"],
    },
    {
        "outage_id": "sample-outage-violated",
        "severity": "critical",
        "status": "resolved",
        "started_at": "2026-01-16T08:00:00+00:00",
        "resolved_at": "2026-01-16T09:30:00+00:00",
        "mttr_minutes": 90,
        "threshold_minutes": 60,
        "affected_services": ["core-network"],
    },
    {
        "outage_id": "sample-outage-open",
        "severity": "medium",
        "status": "open",
        "started_at": "2026-01-17T12:00:00+00:00",
        "resolved_at": None,
        "mttr_minutes": None,
        "threshold_minutes": 120,
        "affected_services": ["messaging"],
    },
)


def get_sample_outage_summaries() -> list[OutageSummary]:
    """Return fresh copies of the stable resolved and open outage samples."""
    return deepcopy(list(_SAMPLE_OUTAGE_SUMMARIES))
