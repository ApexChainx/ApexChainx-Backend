"""Tests for the SLA operations webhook monitoring router.

These tests exercise ``app.sla_operations.api.webhook_monitoring_router`` via a
FastAPI ``TestClient``. They are deterministic and isolated from core
application state: no database, no network, and no global mutable fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api import webhook_monitoring_router as wmr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """Return a TestClient bound to an app exposing only the target router."""
    app = FastAPI()
    app.include_router(wmr.router)
    return TestClient(app)


@pytest.fixture()
def window() -> Dict[str, str]:
    """A valid, deterministic monitoring window (ISO-8601, UTC)."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }


@pytest.fixture()
def valid_payload(window: Dict[str, str]) -> Dict[str, Any]:
    """A minimal valid request body for POST /view."""
    return {
        **window,
        "webhook_ids": ["wh_alpha", "wh_beta"],
        "include_successful": True,
        "limit": 50,
    }


# ---------------------------------------------------------------------------
# POST /view - success path
# ---------------------------------------------------------------------------


def test_view_returns_monitoring_view_shape(
    client: TestClient, valid_payload: Dict[str, Any]
) -> None:
    response = client.post("/view", json=valid_payload)

    assert response.status_code == 200
    body = response.json()

    # Top-level shape of WebhookMonitoringView.
    assert set(body.keys()) == {
        "window_start",
        "window_end",
        "include_successful",
        "limit",
        "entries",
        "total",
    }
    assert body["include_successful"] is True
    assert body["limit"] == 50
    assert isinstance(body["entries"], list)
    assert isinstance(body["total"], int)
    assert body["total"] == len(body["entries"])

    # Echoed window is normalized to ISO-8601 strings.
    assert body["window_start"] == valid_payload["window_start"]
    assert body["window_end"] == valid_payload["window_end"]


def test_view_entries_have_expected_keys(
    client: TestClient, valid_payload: Dict[str, Any]
) -> None:
    response = client.post("/view", json=valid_payload)
    assert response.status_code == 200

    entries: List[Dict[str, Any]] = response.json()["entries"]
    assert entries, "expected at least one monitoring entry for the fixture window"

    expected_keys = {
        "webhook_id",
        "state",
        "deliveries",
        "failures",
        "success_rate",
        "last_attempt_at",
    }
    for entry in entries:
        assert set(entry.keys()) == expected_keys
        assert isinstance(entry["webhook_id"], str)
        assert isinstance(entry["deliveries"], int)
        assert isinstance(entry["failures"], int)
        assert 0.0 <= entry["success_rate"] <= 1.0


def test_view_is_deterministic(
    client: TestClient, valid_payload: Dict[str, Any]
) -> None:
    first = client.post("/view", json=valid_payload)
    second = client.post("/view", json=valid_payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


def test_health_returns_deterministic_entries(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()

    assert "entries" in body
    entries: List[Dict[str, Any]] = body["entries"]
    assert isinstance(entries, list)
    assert entries, "expected deterministic health entries from fixtures"

    expected_keys = {"webhook_id", "state", "healthy", "checked_at"}
    for entry in entries:
        assert set(entry.keys()) == expected_keys
        assert isinstance(entry["webhook_id"], str)
        assert isinstance(entry["healthy"], bool)
        assert entry["state"] in {"healthy", "degraded", "down"}


def test_health_is_deterministic(client: TestClient) -> None:
    first = client.get("/health")
    second = client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_view_with_empty_webhook_ids(
    client: TestClient, valid_payload: Dict[str, Any]
) -> None:
    payload = {**valid_payload, "webhook_ids": []}
    response = client.post("/view", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == []
    assert body["total"] == 0


def test_view_excluding_successful(
    client: TestClient, valid_payload: Dict[str, Any]
) -> None:
    payload = {**valid_payload, "include_successful": False}
    response = client.post("/view", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["include_successful"] is False
    # When excluding successful deliveries, no entry may report a perfect rate.
    for entry in body["entries"]:
        assert entry["success_rate"] < 1.0


@pytest.mark.parametrize("limit", [1, 500])
def test_view_limit_boundaries(
    client: TestClient, valid_payload: Dict[str, Any], limit: int
) -> None:
    payload = {**valid_payload, "limit": limit}
    response = client.post("/view", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == limit
    assert len(body["entries"]) <= limit


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_view_rejects_inverted_window(
    client: TestClient, valid_payload: Dict[str, Any]
) -> None:
    payload = {
        **valid_payload,
        "window_start": valid_payload["window_end"],
        "window_end": valid_payload["window_start"],
    }
    response = client.post("/view", json=payload)

    assert response.status_code == 422


def test_view_rejects_equal_window_bounds(
    client: TestClient, valid_payload: Dict[str, Any]
) -> None:
    payload = {
        **valid_payload,
        "window_end": valid_payload["window_start"],
    }
    response = client.post("/view", json=payload)

    assert response.status_code == 422


def test_view_rejects_invalid_state_enum(
    client: TestClient, valid_payload: Dict[str, Any]
) -> None:
    payload = {**valid_payload, "state": "not-a-real-state"}
    response = client.post("/view", json=payload)

    assert response.status_code == 422


def test_view_rejects_missing_required_fields(client: TestClient) -> None:
    response = client.post("/view", json={})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    missing = {item["loc"][-1] for item in detail}
    assert {"window_start", "window_end"} <= missing


def test_view_rejects_missing_window_end(
    client: TestClient, window: Dict[str, str]
) -> None:
    response = client.post("/view", json={"window_start": window["window_start"]})

    assert response.status_code == 422
    detail = response.json()["detail"]
    missing = {item["loc"][-1] for item in detail}
    assert "window_end" in missing


@pytest.mark.parametrize("limit", [0, 501])
def test_view_rejects_out_of_range_limit(
    client: TestClient, valid_payload: Dict[str, Any], limit: int
) -> None:
    payload = {**valid_payload, "limit": limit}
    response = client.post("/view", json=payload)

    assert response.status_code == 422