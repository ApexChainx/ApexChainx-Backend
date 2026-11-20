"""Deterministic outage-import rows covering accepted and rejected input.

Rows are represented as CSV-style strings with an expected outcome annotation;
the module deliberately does not implement or couple to an import validator.
Each call returns fresh records.
"""

from typing import TypedDict


class BulkImportSample(TypedDict):
    row_number: int
    expected_valid: bool
    expected_errors: tuple[str, ...]
    data: dict[str, str]


def bulk_import_row_fixtures() -> list[BulkImportSample]:
    """Return valid, invalid, duplicate-ID, and boundary outage import rows."""
    base_row = {
        "outage_id": "outage-import-001",
        "site_name": "North Ridge Relay",
        "site_id": "site-north-ridge",
        "severity": "high",
        "status": "resolved",
        "detected_at": "2026-11-18T08:00:00Z",
        "resolved_at": "2026-11-18T08:42:00Z",
        "description": "Backhaul interruption after a fiber cut",
        "affected_services": "4G|5G",
        "affected_subscribers": "1280",
    }

    active_row = {
        "outage_id": "outage-import-002",
        "site_name": "Harbor Exchange",
        "site_id": "site-harbor",
        "severity": "critical",
        "status": "active",
        "detected_at": "2026-11-18T09:15:00Z",
        "resolved_at": "",
        "description": "Power redundancy exhausted during maintenance",
        "affected_services": "voice|data",
        "affected_subscribers": "0",
    }

    invalid_severity = dict(base_row)
    invalid_severity.update(
        {
            "outage_id": "outage-import-003",
            "severity": "urgent",
        }
    )

    invalid_time_order = dict(base_row)
    invalid_time_order.update(
        {
            "outage_id": "outage-import-004",
            "detected_at": "2026-11-18T12:00:00Z",
            "resolved_at": "2026-11-18T11:00:00Z",
        }
    )

    duplicate_id = dict(base_row)
    duplicate_id.update(
        {
            "site_name": "Duplicate North Ridge import",
            "detected_at": "2026-11-19T08:00:00Z",
            "resolved_at": "2026-11-19T08:10:00Z",
        }
    )

    return [
        {
            "row_number": 2,
            "expected_valid": True,
            "expected_errors": (),
            "data": dict(base_row),
        },
        {
            "row_number": 3,
            "expected_valid": True,
            "expected_errors": (),
            "data": active_row,
        },
        {
            "row_number": 4,
            "expected_valid": False,
            "expected_errors": ("invalid_severity",),
            "data": invalid_severity,
        },
        {
            "row_number": 5,
            "expected_valid": False,
            "expected_errors": ("resolved_before_detected",),
            "data": invalid_time_order,
        },
        {
            "row_number": 6,
            "expected_valid": False,
            "expected_errors": ("duplicate_outage_id",),
            "data": duplicate_id,
        },
    ]
