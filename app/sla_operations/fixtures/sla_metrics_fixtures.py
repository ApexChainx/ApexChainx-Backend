"""Deterministic SLA metric samples for workspace tests and demos.

Use ``get_sla_metrics_fixtures`` to receive fresh records that can be safely
modified by a consumer without changing later fixture results.
"""

from typing import Literal, TypedDict


class SLAMetricsFixture(TypedDict):
    scenario: str
    period: str
    severity: Literal["critical", "high", "medium", "low"]
    total_outages: int
    violated_outages: int
    avg_mttr_minutes: float
    availability_percentage: float
    rewards: float
    penalties: float


_SLA_METRICS: tuple[SLAMetricsFixture, ...] = (
    {
        "scenario": "healthy_month",
        "period": "2025-01",
        "severity": "low",
        "total_outages": 4,
        "violated_outages": 0,
        "avg_mttr_minutes": 18.5,
        "availability_percentage": 99.98,
        "rewards": 240.0,
        "penalties": 0.0,
    },
    {
        "scenario": "critical_sla_breach",
        "period": "2025-02",
        "severity": "critical",
        "total_outages": 3,
        "violated_outages": 2,
        "avg_mttr_minutes": 95.0,
        "availability_percentage": 98.7,
        "rewards": 0.0,
        "penalties": 1250.0,
    },
    {
        "scenario": "no_outages",
        "period": "2025-03",
        "severity": "medium",
        "total_outages": 0,
        "violated_outages": 0,
        "avg_mttr_minutes": 0.0,
        "availability_percentage": 100.0,
        "rewards": 0.0,
        "penalties": 0.0,
    },
    {
        "scenario": "all_outages_violated",
        "period": "2025-04",
        "severity": "high",
        "total_outages": 2,
        "violated_outages": 2,
        "avg_mttr_minutes": 60.0,
        "availability_percentage": 99.2,
        "rewards": 0.0,
        "penalties": 400.0,
    },
)


def get_sla_metrics_fixtures() -> list[SLAMetricsFixture]:
    """Return fresh metric records spanning healthy, breached, and empty periods."""
    return [record.copy() for record in _SLA_METRICS]
