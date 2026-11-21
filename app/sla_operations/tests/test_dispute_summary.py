from datetime import UTC, datetime, timedelta

import pytest

from app.sla_operations.services.dispute_summary import FindingCode, summarize_disputes

FLAGGED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _dispute(status: str, **overrides: object) -> dict[str, object]:
    dispute: dict[str, object] = {"status": status, "flagged_at": FLAGGED_AT}
    dispute.update(overrides)
    return dispute


def test_state_counts_and_rates():
    disputes = [
        _dispute("resolved"),
        _dispute("RESOLVED"),
        _dispute("rejected"),
        _dispute("pending"),
    ]

    report = summarize_disputes(disputes)

    assert report.total == 4
    assert report.by_status == {"pending": 1, "resolved": 2, "rejected": 1, "unknown": 0}
    assert report.status_ratios == {"pending": 0.25, "resolved": 0.5, "rejected": 0.25, "unknown": 0.0}
    assert (report.resolution_rate, report.rejection_rate, report.pending_rate) == (0.5, 0.25, 0.25)
    assert report.findings == []


def test_resolution_indicators_and_durations():
    disputes = [
        _dispute(
            "resolved",
            resolved_at=FLAGGED_AT + timedelta(minutes=10),
            resolved_by="engineer@example.com",
            resolution_notes="Recomputed SLA",
            proposed_sla_result_id=7,
        ),
        _dispute(
            "resolved",
            resolved_at=FLAGGED_AT + timedelta(minutes=20),
            resolved_by="admin@example.com",
            resolution_notes="Applied baseline",
            baseline_sla_result_id=3,
        ),
    ]

    report = summarize_disputes(disputes)
    indicators = report.indicators

    assert indicators.with_resolved_at == 2
    assert indicators.with_resolved_by == 2
    assert indicators.with_resolution_notes == 2
    assert indicators.with_proposed_result == 1
    assert indicators.with_baseline_result == 1
    assert indicators.terminal_complete == 2
    assert indicators.terminal_incomplete == 0
    assert indicators.average_resolution_minutes == 15.0
    assert indicators.min_resolution_minutes == 10.0
    assert indicators.max_resolution_minutes == 20.0


def test_incomplete_terminal_and_pending_metadata_are_counted():
    disputes = [
        _dispute("resolved", resolved_by="engineer@example.com"),
        _dispute("pending", resolved_by="engineer@example.com"),
    ]

    indicators = summarize_disputes(disputes).indicators

    assert indicators.terminal_incomplete == 1
    assert indicators.pending_with_resolution_metadata == 1
    assert indicators.terminal_complete == 0
    assert indicators.average_resolution_minutes is None


def test_invalid_rows_and_timestamps_produce_findings():
    disputes = [
        None,
        _dispute("resolved", flagged_at="not-a-date", resolved_at="2026-01-01T00:10:00Z"),
        _dispute("resolved", resolved_at=FLAGGED_AT - timedelta(minutes=1)),
    ]

    report = summarize_disputes(disputes)

    assert report.total == 3
    assert report.by_status["unknown"] == 1
    assert [finding.code for finding in report.findings] == [
        FindingCode.invalid_row,
        FindingCode.invalid_timestamp,
        FindingCode.invalid_resolution_window,
    ]
    assert [finding.source_index for finding in report.findings] == [0, 1, 2]


@pytest.mark.parametrize("disputes", [None, [], 42])
def test_empty_and_non_iterable_inputs_are_safe(disputes):
    report = summarize_disputes(disputes)

    assert report.total == 0
    assert set(report.by_status.values()) == {0}
    assert set(report.status_ratios.values()) == {0.0}
    assert (report.resolution_rate, report.rejection_rate, report.pending_rate) == (0.0, 0.0, 0.0)
    assert report.indicators.average_resolution_minutes is None
    assert report.findings == []


def test_extra_status_labels_and_missing_optional_data_are_safe():
    disputes = [_dispute(" RESOLVED "), {"status": "escalated"}]

    report = summarize_disputes(disputes, statuses=[" escalated "])

    assert report.by_status["resolved"] == 1
    assert report.by_status["escalated"] == 1
    assert list(report.by_status)[-1] == "escalated"
    assert report.indicators.terminal_incomplete == 1
    assert report.findings == []
