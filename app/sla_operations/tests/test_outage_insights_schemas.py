from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.outage_insights import (
    MTTRAggregate,
    OutageInsightGrouping,
    OutageInsightQuery,
    OutageInsightsReport,
    OutageLifecycleStatus,
    OutageSeverityLevel,
    SeverityDistributionEntry,
    StatusBreakdownEntry,
)

START = datetime(2026, 2, 1, tzinfo=UTC)


def mttr_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sample_size": 5,
        "minimum_minutes": 10.0,
        "average_minutes": 20.0,
        "median_minutes": 18.0,
        "p95_minutes": 30.0,
        "maximum_minutes": 30.0,
    }
    payload.update(overrides)
    return payload


def test_report_accepts_representative_distributions_and_mttr() -> None:
    report = OutageInsightsReport(
        report_id="outage-insights-2026-02",
        window_start=START,
        window_end=START + timedelta(days=28),
        generated_at=START + timedelta(days=29),
        total_outages=10,
        severity_distribution=[
            SeverityDistributionEntry(
                severity="critical",
                outage_count=3,
                violation_count=2,
                affected_subscribers=1200,
                percentage=30.0,
            ),
            SeverityDistributionEntry(
                severity="high",
                outage_count=7,
                violation_count=1,
                affected_subscribers=300,
                percentage=70.0,
            ),
        ],
        mttr_aggregate=mttr_payload(),
        status_breakdown=[
            StatusBreakdownEntry(status="active", outage_count=3, percentage=30.0),
            StatusBreakdownEntry(status="resolved", outage_count=7, percentage=70.0),
        ],
    )

    assert report.severity_distribution[0].severity is OutageSeverityLevel.CRITICAL
    assert report.status_breakdown[1].status is OutageLifecycleStatus.RESOLVED
    assert report.mttr_aggregate.p95_minutes == 30.0
    assert report.window_start.tzinfo is UTC


def test_empty_sample_boundary_and_query_enums() -> None:
    aggregate = MTTRAggregate(
        **mttr_payload(
            sample_size=0,
            minimum_minutes=0,
            average_minutes=0,
            median_minutes=0,
            p95_minutes=0,
            maximum_minutes=0,
        )
    )
    query = OutageInsightQuery(
        window_start=START,
        window_end=START + timedelta(days=1),
        grouping="status",
        severities=["critical", "low"],
        statuses=["resolved"],
        limit=1000,
    )

    assert aggregate.sample_size == 0
    assert query.grouping is OutageInsightGrouping.STATUS
    assert query.severities[1] is OutageSeverityLevel.LOW


@pytest.mark.parametrize(
    "overrides",
    [
        {"minimum_minutes": 40.0},
        {"median_minutes": 40.0},
        {"p95_minutes": 40.0},
        {"average_minutes": -1.0},
        {"sample_size": -1},
    ],
)
def test_mttr_aggregate_rejects_inconsistent_statistics(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MTTRAggregate(**mttr_payload(**overrides))


def test_distribution_entries_reject_invalid_severities_and_counts() -> None:
    with pytest.raises(ValidationError):
        SeverityDistributionEntry(
            severity="urgent",
            outage_count=1,
            violation_count=0,
            affected_subscribers=0,
            percentage=100.0,
        )
    with pytest.raises(ValidationError):
        SeverityDistributionEntry(
            severity="high",
            outage_count=1,
            violation_count=2,
            affected_subscribers=0,
            percentage=100.0,
        )
    with pytest.raises(ValidationError):
        StatusBreakdownEntry(status="closed", outage_count=1, percentage=100.0)
    with pytest.raises(ValidationError):
        StatusBreakdownEntry(status="resolved", outage_count=1, percentage=100.1)


def test_report_rejects_inconsistent_totals_and_windows() -> None:
    with pytest.raises(ValidationError):
        OutageInsightsReport(
            report_id="outage-insights-1",
            window_start=START,
            window_end=START,
            generated_at=START,
            total_outages=1,
            severity_distribution=[],
            mttr_aggregate=mttr_payload(),
            status_breakdown=[],
        )
    with pytest.raises(ValidationError):
        OutageInsightsReport(
            report_id="outage-insights-1",
            window_start=START,
            window_end=START + timedelta(days=1),
            generated_at=START,
            total_outages=1,
            severity_distribution=[
                SeverityDistributionEntry(
                    severity="low",
                    outage_count=2,
                    violation_count=0,
                    affected_subscribers=0,
                    percentage=100.0,
                )
            ],
            mttr_aggregate=mttr_payload(),
            status_breakdown=[],
        )
    with pytest.raises(ValidationError):
        OutageInsightQuery(
            window_start=START,
            window_end=START + timedelta(days=1),
            grouping="region",
        )
    with pytest.raises(ValidationError):
        OutageInsightQuery(
            window_start=START,
            window_end=START + timedelta(days=1),
            limit=0,
        )
