from datetime import UTC, datetime, timedelta, timezone as dt_timezone

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.sla_summary import (
    SLASeverityLevel,
    SLASummaryPeriod,
    SLASummaryQuery,
    SLAKPISnapshot,
    SLAOperationKPIs,
    SLAOperationSummary,
    SLATrendPoint,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def kpi_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "total_outages": 10,
        "total_violations": 2,
        "availability_percentage": 99.5,
        "avg_mttr_minutes": 18.0,
        "total_rewards": 300.0,
        "total_penalties": 50.0,
        "net_payout": 250.0,
    }
    payload.update(overrides)
    return payload


def test_summary_accepts_representative_payload_and_normalizes_timezones() -> None:
    point = SLATrendPoint(
        bucket_start=START.astimezone(dt_timezone(timedelta(hours=2))),
        bucket_end=(START + timedelta(days=1)).astimezone(dt_timezone(timedelta(hours=2))),
        total_outages=4,
        violations=1,
        availability_percentage=98.0,
        avg_mttr_minutes=20.0,
        rewards=100.0,
        penalties=20.0,
        net_payout=80.0,
        severity="high",
    )
    summary = SLAOperationSummary(
        summary_id="sla-2026-w1",
        period="weekly",
        window_start=START,
        window_end=START + timedelta(days=7),
        generated_at=START + timedelta(days=7, hours=1),
        kpis=kpi_payload(),
        trend=[point],
    )

    assert summary.period is SLASummaryPeriod.WEEKLY
    assert summary.trend[0].severity is SLASeverityLevel.HIGH
    assert summary.kpis.avg_mttr_minutes == 18.0
    assert summary.trend[0].bucket_start == START


def test_kpis_accept_legacy_mttr_alias_and_zero_boundaries() -> None:
    legacy = SLAOperationKPIs(
        **{key: value for key, value in kpi_payload().items() if key != "avg_mttr_minutes"},
        avg_mttr=12.0,
    )
    empty = SLAOperationKPIs(**kpi_payload(total_outages=0, total_violations=0, availability_percentage=0.0))

    assert legacy.avg_mttr_minutes == 12.0
    assert empty.total_outages == 0
    assert empty.availability_percentage == 0.0


def test_snapshot_and_query_validate_windows_and_limits() -> None:
    snapshot = SLAKPISnapshot(
        snapshot_id="snapshot-1",
        captured_at=START + timedelta(days=7),
        window_start=START,
        window_end=START + timedelta(days=7),
        kpis=kpi_payload(),
        checksum="abc123def456",
    )
    query = SLASummaryQuery(
        period="daily",
        window_start=START,
        window_end=START + timedelta(seconds=1),
        granularity="hour",
        severity="critical",
        limit=1,
    )

    assert snapshot.checksum == "abc123def456"
    assert query.severity is SLASeverityLevel.CRITICAL
    assert query.limit == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"total_outages": -1},
        {"total_outages": 1, "total_violations": 2},
        {"availability_percentage": 100.1},
        {"avg_mttr_minutes": -0.1},
        {"total_rewards": -1.0},
    ],
)
def test_kpis_reject_invalid_metrics(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SLAOperationKPIs(**kpi_payload(**overrides))


def test_models_reject_invalid_enums_timezones_and_windows() -> None:
    with pytest.raises(ValidationError):
        SLAOperationSummary(
            summary_id="sla-1",
            period="yearly",
            window_start=START,
            window_end=START + timedelta(days=1),
            generated_at=START,
            kpis=kpi_payload(),
        )
    with pytest.raises(ValidationError):
        SLATrendPoint(
            bucket_start=datetime(2026, 1, 1),
            bucket_end=datetime(2026, 1, 2),
            total_outages=0,
            violations=0,
            availability_percentage=100.0,
            avg_mttr_minutes=0.0,
            rewards=0.0,
            penalties=0.0,
            net_payout=0.0,
        )
    with pytest.raises(ValidationError):
        SLAOperationSummary(
            summary_id="sla-1",
            period="daily",
            window_start=START + timedelta(days=1),
            window_end=START,
            generated_at=START,
            kpis=kpi_payload(),
        )
    with pytest.raises(ValidationError):
        SLASummaryQuery(
            period="daily",
            window_start=START,
            window_end=START + timedelta(days=1),
            limit=1001,
        )
