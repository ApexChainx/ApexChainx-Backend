"""Tests for the isolated SLA operations outage insights router.

These tests exercise the workspace-local router module
``app.sla_operations.api.outage_insights_router`` without touching the core
application router. A standalone FastAPI app is constructed that includes only
this router, and direct handler calls are used for edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api import outage_insights_router as router_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(router_module.router)
    return TestClient(app)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture()
def client() -> TestClient:
    return _build_client()


@pytest.fixture()
def sample_window() -> Dict[str, str]:
    end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
    start = end - timedelta(days=7)
    return {"start": _iso(start), "end": _iso(end)}


# ---------------------------------------------------------------------------
# Module import / structure
# ---------------------------------------------------------------------------


def test_module_imports_independently() -> None:
    assert hasattr(router_module, "router")
    assert router_module.router is not None


def test_router_has_expected_routes() -> None:
    paths = {route.path for route in router_module.router.routes}
    assert any("outage" in path for path in paths)


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_summary_endpoint_returns_deterministic_response(
    client: TestClient, sample_window: Dict[str, str]
) -> None:
    response = client.get(
        "/sla-operations/outage-insights/summary",
        params=sample_window,
    )
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    # Deterministic: repeated calls yield identical payloads.
    second = client.get(
        "/sla-operations/outage-insights/summary",
        params=sample_window,
    )
    assert second.status_code == 200
    assert second.json() == payload


def test_summary_endpoint_rejects_invalid_window(client: TestClient) -> None:
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    start = end + timedelta(days=1)  # start after end -> invalid
    response = client.get(
        "/sla-operations/outage-insights/summary",
        params={"start": _iso(start), "end": _iso(end)},
    )
    assert response.status_code == 422


def test_summary_endpoint_rejects_missing_params(client: TestClient) -> None:
    response = client.get("/sla-operations/outage-insights/summary")
    assert response.status_code == 422


def test_trends_endpoint_returns_deterministic_response(
    client: TestClient, sample_window: Dict[str, str]
) -> None:
    response = client.get(
        "/sla-operations/outage-insights/trends",
        params=sample_window,
    )
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    second = client.get(
        "/sla-operations/outage-insights/trends",
        params=sample_window,
    )
    assert second.status_code == 200
    assert second.json() == payload


def test_trends_endpoint_rejects_invalid_window(client: TestClient) -> None:
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    start = end + timedelta(days=1)
    response = client.get(
        "/sla-operations/outage-insights/trends",
        params={"start": _iso(start), "end": _iso(end)},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Direct handler / helper tests
# ---------------------------------------------------------------------------


def _call_summary_handler(
    start: datetime, end: datetime, outages: Optional[List[Dict[str, Any]]] = None
) -> Any:
    """Invoke the summary handler directly, bypassing HTTP layer."""
    handler = getattr(router_module, "get_outage_summary", None)
    if handler is None:
        pytest.skip("summary handler not exposed for direct invocation")
    return handler(start=start, end=end, outages=outages or [])


def test_summary_handler_empty_data_is_deterministic() -> None:
    end = datetime(2024, 1, 31, tzinfo=timezone.utc)
    start = end - timedelta(days=7)
    first = _call_summary_handler(start, end, outages=[])
    second = _call_summary_handler(start, end, outages=[])
    assert first == second


def test_summary_handler_invalid_window_raises() -> None:
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    start = end + timedelta(days=1)
    with pytest.raises(Exception):
        _call_summary_handler(start, end, outages=[])


def test_summary_handler_with_sample_outages() -> None:
    end = datetime(2024, 1, 31, tzinfo=timezone.utc)
    start = end - timedelta(days=7)
    outages = [
        {
            "id": "outage-1",
            "started_at": _iso(start + timedelta(days=1)),
            "resolved_at": _iso(start + timedelta(days=1, hours=2)),
            "severity": "high",
        },
        {
            "id": "outage-2",
            "started_at": _iso(start + timedelta(days=3)),
            "resolved_at": _iso(start + timedelta(days=3, hours=1)),
            "severity": "low",
        },
    ]
    result = _call_summary_handler(start, end, outages=outages)
    assert result is not None
    # Deterministic across repeated invocations.
    assert _call_summary_handler(start, end, outages=outages) == result