"""Tests for the SLA operations workspace export router.

These tests exercise ``app.sla_operations.api.export_router`` in isolation
from the core application. They use a workspace-local FastAPI app so that
no maintainer-owned routers or state are touched.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api import export_router as export_router_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_rows() -> List[Dict[str, Any]]:
    """Deterministic sample rows used across the export tests."""

    return [
        {"id": 1, "name": "alpha", "value": 10},
        {"id": 2, "name": "beta", "value": 20},
        {"id": 3, "name": "gamma", "value": 30},
    ]


@pytest.fixture()
def fixed_timestamp() -> datetime:
    """A stable timestamp so metadata assertions are deterministic."""

    return datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture()
def client() -> TestClient:
    """A TestClient bound to a workspace-local app with only the export router."""

    app = FastAPI()
    app.include_router(export_router_module.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_csv(payload: bytes) -> List[Dict[str, str]]:
    """Parse a CSV byte payload into a list of dict rows."""

    text = payload.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


# ---------------------------------------------------------------------------
# prepare_export_metadata unit tests
# ---------------------------------------------------------------------------


def test_prepare_export_metadata_returns_expected_fields(
    sample_rows: List[Dict[str, Any]], fixed_timestamp: datetime
) -> None:
    metadata = export_router_module.prepare_export_metadata(
        rows=sample_rows,
        resource="incidents",
        generated_at=fixed_timestamp,
    )

    assert metadata["row_count"] == len(sample_rows)
    assert metadata["resource"] == "incidents"
    assert metadata["generated_at"] == fixed_timestamp
    assert metadata["filename"].startswith("incidents_")
    assert metadata["filename"].endswith(".csv")
    assert metadata["content_type"] == "text/csv"


def test_prepare_export_metadata_filename_is_deterministic(
    sample_rows: List[Dict[str, Any]], fixed_timestamp: datetime
) -> None:
    first = export_router_module.prepare_export_metadata(
        rows=sample_rows,
        resource="incidents",
        generated_at=fixed_timestamp,
    )
    second = export_router_module.prepare_export_metadata(
        rows=sample_rows,
        resource="incidents",
        generated_at=fixed_timestamp,
    )

    assert first["filename"] == second["filename"]
    # Timestamp is embedded in the filename in a stable, sortable format.
    assert "20240102T030405Z" in first["filename"]


def test_prepare_export_metadata_empty_rows(fixed_timestamp: datetime) -> None:
    metadata = export_router_module.prepare_export_metadata(
        rows=[],
        resource="incidents",
        generated_at=fixed_timestamp,
    )

    assert metadata["row_count"] == 0
    assert metadata["content_type"] == "text/csv"
    assert metadata["filename"].startswith("incidents_")


# ---------------------------------------------------------------------------
# Export endpoint integration tests
# ---------------------------------------------------------------------------


def test_export_endpoint_returns_csv(
    client: TestClient, sample_rows: List[Dict[str, Any]]
) -> None:
    response = client.post(
        "/sla-operations/export",
        json={"resource": "incidents", "rows": sample_rows},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers.get("content-disposition", "")

    parsed = _parse_csv(response.content)
    assert len(parsed) == len(sample_rows)
    assert parsed[0]["name"] == "alpha"
    assert parsed[1]["name"] == "beta"
    assert parsed[2]["name"] == "gamma"
    # Header order should match the first row's key order.
    assert list(parsed[0].keys()) == ["id", "name", "value"]


def test_export_endpoint_empty_rows(client: TestClient) -> None:
    response = client.post(
        "/sla-operations/export",
        json={"resource": "incidents", "rows": []},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    # Empty export still yields a valid (empty) CSV body.
    assert response.content == b""


def test_export_endpoint_rejects_missing_resource(client: TestClient) -> None:
    response = client.post(
        "/sla-operations/export",
        json={"rows": [{"id": 1}]},
    )

    assert response.status_code == 422


def test_export_endpoint_rejects_blank_resource(
    client: TestClient, sample_rows: List[Dict[str, Any]]
) -> None:
    response = client.post(
        "/sla-operations/export",
        json={"resource": "   ", "rows": sample_rows},
    )

    assert response.status_code == 422