"""Tests for the SLA operations outage insights router.

These tests exercise the router in isolation by mounting it on a fresh
``FastAPI`` application. They intentionally avoid importing ``app.main`` or
using any shared client fixtures so that the workspace stays decoupled from
core application state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api import outage_insights_router
from app.sla_operations.api.outage_insights_router import (
    OutageInsightsReport,
    WorkspaceErrorResponse,
)


@pytest.fixture()
def client() -> TestClient:
    """Build a router-only FastAPI app and return a TestClient for it."""
    app = FastAPI()
    app.include_router(outage_insights_router.router)
    return TestClient(app)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _valid_query_params() -> dict[str, str]:
    """Return a deterministic, valid set of query parameters."""
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    window_end = window_start + timedelta(days=7)
    return {
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "limit": "10",
    }


def test_success_returns_report_envelope(client: TestClient) -> None:
    """A valid query returns 200 with the OutageInsightsReport envelope shape."""
    response = client.get(
        outage_insights_router.ROUTER_PREFIX + "/insights",
        params=_valid_query_params(),
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    # Validate the response against the declared response model so that the
    # envelope shape stays in sync with the router contract.
    report = OutageInsightsReport.model_validate(payload)

    expected_fields = set(OutageInsightsReport.model_fields.keys())
    assert set(payload.keys()) == expected_fields

    # Basic sanity checks on the report contents.
    assert report.window_start <= report.window_end
    assert report.total_outages >= 0
    assert isinstance(report.insights, list)


def test_success_with_default_limit(client: TestClient) -> None:
    """Omitting the optional limit uses the router default and still succeeds."""
    params = _valid_query_params()
    params.pop("limit")

    response = client.get(
        outage_insights_router.ROUTER_PREFIX + "/insights",
        params=params,
    )

    assert response.status_code == 200, response.text
    OutageInsightsReport.model_validate(response.json())


@pytest.mark.parametrize(
    "missing_field",
    ["window_start", "window_end"],
)
def test_missing_required_window_param_returns_422(
    client: TestClient, missing_field: str
) -> None:
    """Missing window_start or window_end yields a 422 validation error."""
    params = _valid_query_params()
    params.pop(missing_field)

    response = client.get(
        outage_insights_router.ROUTER_PREFIX + "/insights",
        params=params,
    )

    assert response.status_code == 422, response.text


def test_window_end_before_window_start_returns_422(client: TestClient) -> None:
    """A window_end earlier than window_start is rejected with 422."""
    window_start = datetime(2024, 1, 8, 0, 0, 0, tzinfo=timezone.utc)
    window_end = window_start - timedelta(days=1)

    params = {
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "limit": "10",
    }

    response = client.get(
        outage_insights_router.ROUTER_PREFIX + "/insights",
        params=params,
    )

    assert response.status_code == 422, response.text


def test_window_end_equal_to_window_start_returns_422(client: TestClient) -> None:
    """A zero-length window (window_end == window_start) is rejected with 422."""
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    params = {
        "window_start": _iso(window_start),
        "window_end": _iso(window_start),
        "limit": "10",
    }

    response = client.get(
        outage_insights_router.ROUTER_PREFIX + "/insights",
        params=params,
    )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize("limit", ["0", "-1", "1000"])
def test_limit_out_of_range_returns_422(client: TestClient, limit: str) -> None:
    """Limits outside the allowed range are rejected with 422."""
    params = _valid_query_params()
    params["limit"] = limit

    response = client.get(
        outage_insights_router.ROUTER_PREFIX + "/insights",
        params=params,
    )

    assert response.status_code == 422, response.text


def test_unknown_enum_value_returns_422(client: TestClient) -> None:
    """An unknown enum value for a query parameter is rejected with 422."""
    params = _valid_query_params()
    params["severity"] = "not-a-real-severity"

    response = client.get(
        outage_insights_router.ROUTER_PREFIX + "/insights",
        params=params,
    )

    assert response.status_code == 422, response.text


def test_error_mapping_returns_workspace_error_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raised WorkspaceError is mapped to the WorkspaceErrorResponse shape."""
    error = outage_insights_router.WorkspaceError(
        code="insights_unavailable",
        message="Insights service is temporarily unavailable.",
        status_code=503,
    )

    def _raise_error(*args, **kwargs):  # noqa: ANN002, ANN003
        raise error

    monkeypatch.setattr(
        outage_insights_router,
        "build_outage_insights_report",
        _raise_error,
    )

    response = client.get(
        outage_insights_router.ROUTER_PREFIX + "/insights",
        params=_valid_query_params(),
    )

    assert response.status_code == 503, response.text
    payload = response.json()

    error_response = WorkspaceErrorResponse.model_validate(payload)
    assert error_response.code == "insights_unavailable"
    assert error_response.message == "Insights service is temporarily unavailable."
    assert set(payload.keys()) == set(WorkspaceErrorResponse.model_fields.keys())