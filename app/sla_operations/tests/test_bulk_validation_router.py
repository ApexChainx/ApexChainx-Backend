"""Tests for the SLA operations bulk validation router.

These tests exercise request handling and error mapping for
``app.sla_operations.api.bulk_validation_router``. They are intentionally
isolated from core application state: a fresh FastAPI app is constructed per
test module and only the workspace router is mounted.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api import bulk_validation_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Return a TestClient bound to an app that only mounts the workspace router."""

    app = FastAPI()
    app.include_router(bulk_validation_router.router)
    return TestClient(app)


def _valid_row(row_index: int = 0, **overrides: Any) -> Dict[str, Any]:
    """Build a deterministic, valid bulk validation row."""

    row: Dict[str, Any] = {
        "row_index": row_index,
        "sla_id": f"sla-{row_index:04d}",
        "severity": "high",
        "metric": "uptime",
        "value": 99.5,
    }
    row.update(overrides)
    return row


def _preview_payload(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"action": "preview", "rows": rows}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_preview_valid_payload_returns_items_matching_summary(client: TestClient) -> None:
    rows = [_valid_row(i) for i in range(5)]
    response = client.post("/bulk-validation", json=_preview_payload(rows))

    assert response.status_code == 200, response.text
    body = response.json()

    # Response envelope shape.
    assert "items" in body
    assert "summary" in body
    assert "issues" in body

    items = body["items"]
    summary = body["summary"]

    assert isinstance(items, list)
    assert len(items) == len(rows)
    assert summary["total_records"] == len(rows)
    assert len(items) == summary["total_records"]

    # Row indices are unique and preserved.
    row_indices = [item["row_index"] for item in items]
    assert len(row_indices) == len(set(row_indices))
    assert sorted(row_indices) == list(range(len(rows)))

    # No error issues for a fully valid payload.
    assert body["issues"] == [] or all(
        issue.get("severity") != "error" for issue in body["issues"]
    )


def test_preview_empty_rows_returns_422(client: TestClient) -> None:
    response = client.post("/bulk-validation", json=_preview_payload([]))
    assert response.status_code == 422, response.text


def test_preview_malformed_rows_non_list_returns_422(client: TestClient) -> None:
    response = client.post(
        "/bulk-validation",
        json={"action": "preview", "rows": "not-a-list"},
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Invalid rows
# ---------------------------------------------------------------------------


def test_missing_required_field_produces_error_issue(client: TestClient) -> None:
    bad_row = _valid_row(0)
    bad_row.pop("sla_id")
    rows = [bad_row, _valid_row(1)]

    response = client.post("/bulk-validation", json=_preview_payload(rows))
    assert response.status_code == 200, response.text
    body = response.json()

    error_issues = [i for i in body["issues"] if i.get("severity") == "error"]
    assert error_issues, "expected at least one error issue for missing field"
    assert any(issue.get("row_index") == 0 for issue in error_issues)
    # Summary should reflect that not all records are valid.
    assert body["summary"]["total_records"] == len(rows)
    assert body["summary"].get("valid_records", 0) < len(rows)


def test_invalid_severity_produces_error_issue(client: TestClient) -> None:
    rows = [_valid_row(0, severity="catastrophic"), _valid_row(1)]

    response = client.post("/bulk-validation", json=_preview_payload(rows))
    assert response.status_code == 200, response.text
    body = response.json()

    error_issues = [i for i in body["issues"] if i.get("severity") == "error"]
    assert error_issues
    assert any(issue.get("row_index") == 0 for issue in error_issues)


def test_duplicate_ids_produce_error_issue(client: TestClient) -> None:
    rows = [
        _valid_row(0, sla_id="dup-sla"),
        _valid_row(1, sla_id="dup-sla"),
        _valid_row(2, sla_id="unique-sla"),
    ]

    response = client.post("/bulk-validation", json=_preview_payload(rows))
    assert response.status_code == 200, response.text
    body = response.json()

    error_issues = [i for i in body["issues"] if i.get("severity") == "error"]
    assert error_issues, "expected duplicate id to be reported as an error issue"
    flagged = {issue.get("row_index") for issue in error_issues}
    # At least one of the duplicate rows must be flagged.
    assert flagged & {0, 1}


def test_invalid_rows_do_not_raise_and_are_deterministic(client: TestClient) -> None:
    rows = [
        _valid_row(0, severity="nope"),
        _valid_row(1, sla_id="dup"),
        _valid_row(2, sla_id="dup"),
    ]
    payload = _preview_payload(rows)

    first = client.post("/bulk-validation", json=payload)
    second = client.post("/bulk-validation", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


# ---------------------------------------------------------------------------
# Error envelope shape
# ---------------------------------------------------------------------------


def test_error_envelope_shape_for_malformed_payload(client: TestClient) -> None:
    response = client.post(
        "/bulk-validation",
        json={"action": "preview", "rows": "not-a-list"},
    )
    assert response.status_code == 422, response.text

    body = response.json()
    # FastAPI's default 422 envelope uses ``detail``; the workspace error
    # mapping may wrap it further. Accept either shape but require a
    # machine-readable code/message pair when the workspace envelope is used.
    if isinstance(body, dict) and "error" in body:
        error = body["error"]
        assert "code" in error
        assert "message" in error
        assert isinstance(error["code"], str)
        assert isinstance(error["message"], str)
    else:
        assert "detail" in body


def test_error_envelope_shape_for_empty_rows(client: TestClient) -> None:
    response = client.post("/bulk-validation", json=_preview_payload([]))
    assert response.status_code == 422, response.text

    body = response.json()
    if isinstance(body, dict) and "error" in body:
        error = body["error"]
        assert "code" in error
        assert "message" in error
    else:
        assert "detail" in body