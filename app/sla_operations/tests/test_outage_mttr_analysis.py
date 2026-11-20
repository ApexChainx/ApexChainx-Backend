from datetime import UTC, datetime, timedelta

from app.sla_operations.services.outage_mttr_analysis import (
    OutageMTTRAnalyzer,
    OutageMTTRRecord,
    analyze_outage_mttr,
)


def test_analyze_outage_mttr_derives_timestamps_and_groups_distribution():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        {
            "outage_id": "a",
            "severity": "critical",
            "status": "resolved",
            "detected_at": start,
            "resolved_at": start + timedelta(minutes=10),
        },
        OutageMTTRRecord(
            outage_id="b",
            severity="high",
            status="resolved",
            detected_at=start + timedelta(days=1),
            resolved_at=start + timedelta(days=1, minutes=30),
        ),
        {
            "outage_id": "c",
            "severity": "critical",
            "mttr_minutes": 90.0,
            "detected_at": start + timedelta(days=1),
            "resolved_at": start + timedelta(days=1, minutes=90),
        },
        {
            "outage_id": "d",
            "severity": "high",
            "mttr_minutes": 15.0,
            "detected_at": start,
            "resolved_at": start + timedelta(minutes=15),
        },
    ]

    result = analyze_outage_mttr(records, granularity="day")

    assert result.total_records == 4
    assert result.analysed_records == 4
    assert result.excluded_records == 0
    assert result.unresolved_records == 0
    assert result.summary.count == 4
    assert result.summary.mean_minutes == 36.25
    assert result.summary.median_minutes == 22.5
    assert result.summary.min_minutes == 10.0
    assert result.summary.max_minutes == 90.0
    assert result.summary.p90_minutes == 90.0
    assert result.summary.p95_minutes == 90.0
    assert {band.label: band.count for band in result.distribution} == {
        "<=15": 2,
        "<=30": 1,
        "<=60": 0,
        "<=120": 1,
        ">120": 0,
    }
    assert [(item.severity, item.summary.mean_minutes) for item in result.by_severity] == [
        ("critical", 50.0),
        ("high", 22.5),
    ]
    assert [(point.period, point.mean_minutes) for point in result.trend] == [
        ("2026-01-01", 12.5),
        ("2026-01-02", 60.0),
    ]
    assert result.trend[0].change_from_previous_mean_minutes is None
    assert result.trend[1].change_from_previous_mean_minutes == 47.5


def test_analyze_outage_mttr_handles_empty_dataset_and_invalid_granularity():
    result = OutageMTTRAnalyzer.analyze([], granularity="hourly")

    assert result.total_records == 0
    assert result.analysed_records == 0
    assert result.excluded_records == 0
    assert result.granularity == "day"
    assert result.summary.count == 0
    assert result.summary.mean_minutes == 0.0
    assert all(band.count == 0 for band in result.distribution)
    assert result.by_severity == []
    assert result.trend == []
    assert result.exclusion_reasons == {}


def test_analyze_outage_mttr_excludes_invalid_samples_without_raising():
    start = datetime(2026, 1, 1)
    naive_start = datetime(2026, 1, 1, 0, 0)
    naive_end = datetime(2026, 1, 1, 0, 0, 5)
    aware_start = start.replace(tzinfo=UTC)
    records = [
        {"outage_id": "missing"},
        {
            "outage_id": "negative",
            "mttr_minutes": -5.0,
            "detected_at": aware_start,
            "resolved_at": aware_start + timedelta(minutes=1),
        },
        {
            "outage_id": "backwards",
            "detected_at": aware_start,
            "resolved_at": aware_start - timedelta(minutes=2),
        },
        {"outage_id": "unresolved", "status": "active", "detected_at": aware_start, "mttr_minutes": 12.0},
        object(),
        "not-an-outage",
        {"outage_id": "naive", "detected_at": naive_start, "resolved_at": naive_end},
    ]

    result = analyze_outage_mttr(records)

    assert result.total_records == 7
    assert result.analysed_records == 2
    assert result.excluded_records == 5
    assert result.unresolved_records == 2
    assert result.exclusion_reasons == {
        "invalid_record": 2,
        "missing_mttr": 1,
        "negative_duration": 1,
        "negative_mttr": 1,
    }
    assert result.summary.count == 2
    assert result.summary.mean_minutes == 6.04
