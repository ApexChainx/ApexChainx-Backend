from datetime import UTC, datetime

import pytest

from app.sla_operations.services.bulk_import_validation import (
    FindingCode,
    FindingLevel,
    validate_bulk_import_rows,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "outage-1",
        "site_name": "Site A",
        "severity": "high",
        "status": "open",
        "detected_at": "2026-01-01T00:00:00Z",
        "description": "Fiber cut",
        "affected_services": ["core-api"],
    }
    row.update(overrides)
    return row


def test_valid_rows_are_accepted_in_order():
    rows = [_row(), _row(id="outage-2", site_name="Site B", severity="critical")]

    report = validate_bulk_import_rows(rows)

    assert report.valid is True
    assert (report.total_rows, report.valid_rows, report.invalid_rows) == (2, 2, 0)
    assert report.accepted_ids == ["outage-1", "outage-2"]
    assert [result.row for result in report.rows] == [0, 1]
    assert report.findings == []


def test_invalid_rows_report_structured_codes_without_raising():
    rows = [
        _row(site_name="  "),
        _row(id="outage-2", severity="catastrophic"),
        _row(id="outage-3", status="investigating"),
    ]

    report = validate_bulk_import_rows(rows)

    assert report.valid is False
    assert (report.total_rows, report.valid_rows, report.invalid_rows) == (3, 0, 3)
    assert report.accepted_ids == []
    assert [result.valid for result in report.rows] == [False, False, False]
    assert [finding.code for finding in report.findings] == [
        FindingCode.missing_field,
        FindingCode.invalid_value,
        FindingCode.invalid_value,
    ]
    assert [finding.field for finding in report.findings] == ["site_name", "severity", "status"]


def test_duplicate_ids_are_reported_and_not_accepted():
    report = validate_bulk_import_rows([_row(), _row()])

    duplicate = report.findings[0]
    assert duplicate.code is FindingCode.duplicate_id
    assert duplicate.row == 1
    assert duplicate.id == "outage-1"
    assert report.accepted_ids == ["outage-1"]
    assert report.invalid_rows == 1


def test_warnings_do_not_invalidate_a_row():
    row = _row(
        status="resolved",
        detected_at="2026-01-01T00:00:00Z",
        resolved_at="2025-12-31T23:00:00Z",
        future_field="ignored",
    )

    report = validate_bulk_import_rows([row])

    assert report.valid is True
    assert report.rows[0].valid is True
    assert {finding.code for finding in report.findings} == {
        FindingCode.invalid_resolution_window,
        FindingCode.unexpected_field,
    }
    assert all(finding.level is FindingLevel.warning for finding in report.findings)


@pytest.mark.parametrize("rows", [[], None])
def test_empty_datasets_are_safe(rows):
    report = validate_bulk_import_rows(rows)

    assert report.valid is False
    assert (report.total_rows, report.valid_rows, report.invalid_rows) == (0, 0, 0)
    assert report.rows == []
    assert report.accepted_ids == []
    assert [(finding.row, finding.code) for finding in report.findings] == [
        (-1, FindingCode.empty_dataset)
    ]


def test_non_iterable_dataset_is_reported():
    report = validate_bulk_import_rows(42)

    assert report.valid is False
    assert report.total_rows == 0
    assert [(finding.row, finding.code) for finding in report.findings] == [
        (-1, FindingCode.invalid_dataset)
    ]


def test_non_mapping_rows_are_rejected():
    report = validate_bulk_import_rows([42, None])

    assert report.invalid_rows == 2
    assert [finding.code for finding in report.findings] == [
        FindingCode.invalid_type,
        FindingCode.invalid_type,
    ]
    assert [result.row for result in report.rows] == [0, 1]


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"affected_subscribers": -1}, FindingCode.invalid_value),
        ({"affected_subscribers": True}, FindingCode.invalid_value),
        ({"affected_services": []}, FindingCode.invalid_value),
        ({"affected_services": ["core-api", ""]}, FindingCode.invalid_value),
        ({"affected_services": "core-api"}, FindingCode.invalid_type),
        ({"detected_at": "2026-01-01T00:00:00"}, FindingCode.invalid_timestamp),
    ],
)
def test_boundary_values_are_reported(overrides, expected_code):
    report = validate_bulk_import_rows([_row(**overrides)])

    assert report.valid is False
    assert expected_code in {finding.code for finding in report.findings}


def test_zero_subscribers_and_datetime_objects_are_accepted():
    row = _row(
        affected_subscribers=0,
        detected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    report = validate_bulk_import_rows([row])

    assert report.valid is True
    assert report.findings == []


def test_custom_allowed_values_are_case_insensitive():
    row = _row(severity=" sev1 ", status="INVESTIGATING")

    report = validate_bulk_import_rows(
        [row],
        allowed_severities=["SEV1"],
        allowed_statuses=["investigating"],
    )

    assert report.valid is True
    assert report.findings == []


def test_input_rows_are_not_mutated():
    row = _row()
    before = dict(row)

    validate_bulk_import_rows([row])

    assert row == before
