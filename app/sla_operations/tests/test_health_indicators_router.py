"""Tests for the SLA operations health indicators router.

These tests exercise ``app.sla_operations.api.health_indicators_router`` in
isolation by mounting the router on a fresh FastAPI application. No core
application state, database, or maintainer-owned routers are touched.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api.health_indicators_router import router


@pytest.fixture()
def client() -> TestClient:
    """Return a TestClient bound to an app containing only the router."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _indicator(
    name: str,
    state: str,
    *,
    value: float = 1.0,
    threshold: float = 0.5,
    summary: str = "indicator summary",
) -> Dict[str, Any]:
    """Build a single indicator payload for the aggregate endpoint."""
    return {
        "name": name,
        "state": state,
        "value": value,
        "threshold": threshold,
        "summary": summary,
    }


def _aggregate_payload(indicators: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a full aggregate request payload."""
    return {
        "indicators": indicators,
        "summary": "aggregated operational health summary",
    }


def test_aggregate_healthy_only_returns_healthy(client: TestClient) -> None:
    payload = _aggregate_payload(
        [
            _indicator("uptime", "healthy"),
            _indicator("latency", "healthy"),
        ]
    )

    response = client.post("/aggregate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["overall_state"] == "healthy"
    assert body["summary"] == payload["summary"]
    assert len(body["indicators"]) == 2
    assert body["summary_counts"] == {
        "healthy": 2,
        "degraded": 0,
        "unhealthy": 0,
    }


def test_aggregate_with_degraded_indicator_returns_degraded(
    client: TestClient,
) -> None:
    payload = _aggregate_payload(
        [
            _indicator("uptime", "healthy"),
            _indicator("latency", "degraded"),
        ]
    )

    response = client.post("/aggregate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["overall_state"] == "degraded"
    assert body["summary_counts"] == {
        "healthy": 1,
        "degraded": 1,
        "unhealthy": 0,
    }


def test_aggregate_with_unhealthy_indicator_returns_unhealthy(
    client: TestClient,
) -> None:
    payload = _aggregate_payload(
        [
            _indicator("uptime", "healthy"),
            _indicator("latency", "degraded"),
            _indicator("error_rate", "unhealthy"),
        ]
    )

    response = client.post("/aggregate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["overall_state"] == "unhealthy"
    assert body["summary_counts"] == {
        "healthy": 1,
        "degraded": 1,
        "unhealthy": 1,
    }


def test_aggregate_summary_counts_match_indicator_states(
    client: TestClient,
) -> None:
    indicators = [
        _indicator("a", "healthy"),
        _indicator("b", "healthy"),
        _indicator("c", "degraded"),
        _indicator("d", "unhealthy"),
        _indicator("e", "unhealthy"),
    ]

    response = client.post("/aggregate", json=_aggregate_payload(indicators))

    assert response.status_code == 200
    body = response.json()
    assert body["summary_counts"] == {
        "healthy": 2,
        "degraded": 1,
        "unhealthy": 2,
    }
    assert body["overall_state"] == "unhealthy"
    assert len(body["indicators"]) == len(indicators)


def test_aggregate_response_shape(client: TestClient) -> None:
    payload = _aggregate_payload([_indicator("uptime", "healthy")])

    response = client.post("/aggregate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "overall_state",
        "summary",
        "indicators",
        "summary_counts",
    }
    assert set(body["summary_counts"].keys()) == {
        "healthy",
        "degraded",
        "unhealthy",
    }
    indicator = body["indicators"][0]
    assert set(indicator.keys()) == {
        "name",
        "state",
        "value",
        "threshold",
        "summary",
    }
    assert indicator["name"] == "uptime"
    assert indicator["state"] == "healthy"


def test_aggregate_empty_indicators_returns_422(client: TestClient) -> None:
    response = client.post("/aggregate", json=_aggregate_payload([]))

    assert response.status_code == 422


def test_aggregate_blank_summary_returns_422(client: TestClient) -> None:
    payload = _aggregate_payload([_indicator("uptime", "healthy")])
    payload["summary"] = "   "

    response = client.post("/aggregate", json=payload)

    assert response.status_code == 422


def test_aggregate_extra_fields_returns_422(client: TestClient) -> None:
    payload = _aggregate_payload([_indicator("uptime", "healthy")])
    payload["unexpected"] = "value"

    response = client.post("/aggregate", json=payload)

    assert response.status_code == 422


def test_aggregate_invalid_state_returns_422(client: TestClient) -> None:
    payload = _aggregate_payload([_indicator("uptime", "unknown")])

    response = client.post("/aggregate", json=payload)

    assert response.status_code == 422


def test_sample_returns_deterministic_response(client: TestClient) -> None:
    first = client.get("/sample")
    second = client.get("/sample")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    body = first.json()
    assert set(body.keys()) == {
        "overall_state",
        "summary",
        "indicators",
        "summary_counts",
    }
    assert body["overall_state"] in {"healthy", "degraded", "unhealthy"}
    assert isinstance(body["summary"], str)
    assert body["summary"].strip() != ""
    assert isinstance(body["indicators"], list)
    assert body["indicators"]

    counts = {"healthy": 0, "degraded": 0, "unhealthy": 0}
    for indicator in body["indicators"]:
        assert set(indicator.keys()) == {
            "name",
            "state",
            "value",
            "threshold",
            "summary",
        }
        assert indicator["state"] in counts
        counts[indicator["state"]] += 1

    assert body["summary_counts"] == counts