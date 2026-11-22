"""Tests for the SLA operations reconciliation router.

These tests exercise ``app.sla_operations.api.reconciliation_router`` in
isolation. A local ``FastAPI`` application is constructed for each test and
the reconciliation callable dependency is overridden, so no core application
state, database, or network access is required.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api import reconciliation_router as router_module
from app.sla_operations.api.reconciliation_router import (
    WorkspaceAPIError,
    get_reconciliation_service,
    router,
)

RECONCILIATION_PATH = "/sla-operations/reconciliation"


def _build_client(service: Any) -> TestClient:
    """Construct an isolated app with the reconciliation dependency overridden."""

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_reconciliation_service] = lambda: service
    return TestClient(app)


def _valid_payload() -> Dict[str, Any]:
    return {
        "workspace_id": "ws-001",
        "period_start": "2024-01-01T00:00:00Z",
        "period_end": "2024-01-31T23:59:59Z",
        "records": [
            {"record_id": "rec-1", "expected_value": 100.0, "actual_value": 100.0},
            {"record_id": "rec-2", "expected_value": 50.0, "actual_value": 45.0},
        ],
    }


class _StubService:
    """Deterministic stand-in for the reconciliation service callable."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, payload: Any) -> Any:
        self.calls.append(payload if isinstance(payload, dict) else payload.model_dump())
        if self._error is not None:
            raise self._error
        return self._result


def _mismatch_report() -> Dict[str, Any]:
    return {
        "workspace_id": "ws-001",
        "is_reconciled": False,
        "mismatches": [
            {
                "record_id": "rec-2",
                "expected_value": 50.0,
                "actual_value": 45.0,
                "difference": 5.0,
            }
        ],
        "checked_count": 2,
    }


def _reconciled_report() -> Dict[str, Any]:
    return {
        "workspace_id": "ws-001",
        "is_reconciled": True,
        "mismatches": [],
        "checked_count": 2,
    }


def test_reconciliation_with_mismatches_returns_report() -> None:
    service = _StubService(result=_mismatch_report())
    client = _build_client(service)

    response = client.post(RECONCILIATION_PATH, json=_valid_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == "ws-001"
    assert body["is_reconciled"] is False
    assert isinstance(body["mismatches"], list)
    assert len(body["mismatches"]) == 1
    mismatch = body["mismatches"][0]
    assert mismatch["record_id"] == "rec-2"
    assert mismatch["difference"] == pytest.approx(5.0)
    assert body["checked_count"] == 2
    assert len(service.calls) == 1


def test_reconciled_report_has_empty_mismatches() -> None:
    service = _StubService(result=_reconciled_report())
    client = _build_client(service)

    response = client.post(RECONCILIATION_PATH, json=_valid_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["is_reconciled"] is True
    assert body["mismatches"] == []
    assert body["checked_count"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"workspace_id": "ws-001"},
        {
            "workspace_id": "ws-001",
            "period_start": "2024-01-01T00:00:00Z",
            "period_end": "2024-01-31T23:59:59Z",
            "records": "not-a-list",
        },
        {
            "workspace_id": "ws-001",
            "period_start": "2024-01-01T00:00:00Z",
            "period_end": "2024-01-31T23:59:59Z",
            "records": [{"record_id": "rec-1"}],
        },
    ],
)
def test_invalid_payload_returns_422(payload: Dict[str, Any]) -> None:
    service = _StubService(result=_reconciled_report())
    client = _build_client(service)

    response = client.post(RECONCILIATION_PATH, json=payload)

    assert response.status_code == 422
    assert service.calls == []


def test_workspace_api_error_maps_to_configured_status() -> None:
    error = WorkspaceAPIError(
        status_code=409,
        code="reconciliation_conflict",
        message="Conflicting reconciliation in progress",
    )
    service = _StubService(error=error)
    client = _build_client(service)

    response = client.post(RECONCILIATION_PATH, json=_valid_payload())

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "reconciliation_conflict"
    assert "Conflicting reconciliation" in body["message"]


def test_lookup_error_maps_to_404() -> None:
    service = _StubService(error=LookupError("workspace not found"))
    client = _build_client(service)

    response = client.post(RECONCILIATION_PATH, json=_valid_payload())

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert "workspace not found" in body["message"]


def test_value_error_maps_to_422() -> None:
    service = _StubService(error=ValueError("invalid reconciliation window"))
    client = _build_client(service)

    response = client.post(RECONCILIATION_PATH, json=_valid_payload())

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_request"
    assert "invalid reconciliation window" in body["message"]


def test_router_is_registered_on_workspace_module() -> None:
    # Sanity check that the module exposes the router used by the app factory.
    assert router_module.router is router
    assert any(
        getattr(route, "path", None) == RECONCILIATION_PATH
        for route in router.routes
    )