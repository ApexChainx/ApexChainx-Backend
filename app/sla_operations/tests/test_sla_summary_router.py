"""Tests for the SLA operations workspace summary router.

These tests are fully isolated from core application state and the database.
They construct a minimal FastAPI app that mounts only the workspace router and
override the provider dependency with deterministic fakes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api import sla_summary_router as router_module
from app.sla_operations.api.sla_summary_router import (
    SLAOperationSummary,
    WorkspaceAPIError,
    get_sla_summary_provider,
    router,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _valid_summary_payload() -> Dict[str, Any]:
    """A fully populated, valid SLAOperationSummary payload."""
    return {
        "window_start": "2024-01-01T00:00:00+00:00",
        "window_end": "2024-01-02T00:00:00+00:00",
        "total_operations": 42,
        "sla_met": 40,
        "sla_breached": 2,
        "sla_compliance_rate": 0.9523,
        "average_resolution_seconds": 3600.0,
        "p95_resolution_seconds": 7200.0,
        "open_breaches": 1,
    }


def _zero_summary_payload() -> Dict[str, Any]:
    """A valid summary representing an empty window (all zero KPIs)."""
    return {
        "window_start": "2024-01-01T00:00:00+00:00",
        "window_end": "2024-01-02T00:00:00+00:00",
        "total_operations": 0,
        "sla_met": 0,
        "sla_breached": 0,
        "sla_compliance_rate": 0.0,
        "average_resolution_seconds": 0.0,
        "p95_resolution_seconds": 0.0,
        "open_breaches": 0,
    }


class _FakeProvider:
    """Deterministic provider stub.

    Either returns a fixed payload or raises a configured exception.
    """

    def __init__(
        self,
        payload: Optional[Dict[str, Any]] = None,
        exc: Optional[BaseException] = None,
    ) -> None:
        self._payload = payload
        self._exc = exc
        self.calls: List[Dict[str, Any]] = []

    def get_summary(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        assert self._payload is not None
        return self._payload


def _build_client(provider: Any) -> TestClient:
    """Build an isolated TestClient with the provider dependency overridden."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_sla_summary_provider] = lambda: provider
    return TestClient(app)


@pytest.fixture
def valid_client() -> TestClient:
    return _build_client(_FakeProvider(payload=_valid_summary_payload()))


@pytest.fixture
def zero_client() -> TestClient:
    return _build_client(_FakeProvider(payload=_zero_summary_payload()))


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


def test_summary_returns_200_with_valid_payload(valid_client: TestClient) -> None:
    response = valid_client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-01T00:00:00+00:00",
            "window_end": "2024-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] is not None

    # The data payload must validate against the response model.
    summary = SLAOperationSummary.model_validate(body["data"])
    assert summary.total_operations == 42
    assert summary.sla_met == 40
    assert summary.sla_breached == 2
    assert summary.open_breaches == 1


def test_summary_zero_kpis_still_validates(zero_client: TestClient) -> None:
    response = zero_client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-01T00:00:00+00:00",
            "window_end": "2024-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    summary = SLAOperationSummary.model_validate(body["data"])
    assert summary.total_operations == 0
    assert summary.sla_compliance_rate == 0.0


def test_summary_passes_query_params_to_provider() -> None:
    provider = _FakeProvider(payload=_valid_summary_payload())
    client = _build_client(provider)

    response = client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-01T00:00:00+00:00",
            "window_end": "2024-01-02T00:00:00+00:00",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call.get("limit") == 10


# ---------------------------------------------------------------------------
# Validation errors (422 from request validation)
# ---------------------------------------------------------------------------


def test_summary_invalid_window_order_returns_422(valid_client: TestClient) -> None:
    response = valid_client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-02T00:00:00+00:00",
            "window_end": "2024-01-01T00:00:00+00:00",
        },
    )

    assert response.status_code == 422


def test_summary_equal_window_bounds_returns_422(valid_client: TestClient) -> None:
    response = valid_client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-01T00:00:00+00:00",
            "window_end": "2024-01-01T00:00:00+00:00",
        },
    )

    assert response.status_code == 422


def test_summary_out_of_range_limit_returns_422(valid_client: TestClient) -> None:
    response = valid_client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-01T00:00:00+00:00",
            "window_end": "2024-01-02T00:00:00+00:00",
            "limit": 100000,
        },
    )

    assert response.status_code == 422


def test_summary_negative_limit_returns_422(valid_client: TestClient) -> None:
    response = valid_client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-01T00:00:00+00:00",
            "window_end": "2024-01-02T00:00:00+00:00",
            "limit": -1,
        },
    )

    assert response.status_code == 422


def test_summary_missing_required_params_returns_422(valid_client: TestClient) -> None:
    response = valid_client.get("/sla-operations/summary")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Provider error mapping
# ---------------------------------------------------------------------------


def test_summary_workspace_api_error_maps_to_configured_status() -> None:
    exc = WorkspaceAPIError(
        message="upstream unavailable",
        status_code=503,
        code="upstream_unavailable",
    )
    client = _build_client(_FakeProvider(exc=exc))

    response = client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-01T00:00:00+00:00",
            "window_end": "2024-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "upstream_unavailable"


def test_summary_lookup_error_maps_to_404() -> None:
    client = _build_client(_FakeProvider(exc=LookupError("not found")))

    response = client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-01T00:00:00+00:00",
            "window_end": "2024-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False


def test_summary_value_error_maps_to_422() -> None:
    client = _build_client(_FakeProvider(exc=ValueError("bad value")))

    response = client.get(
        "/sla-operations/summary",
        params={
            "window_start": "2024-01-01T00:00:00+00:00",
            "window_end": "2024-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
```

Note: The above test module assumes the following public names exist in `app/sla_operations/api/sla_summary_router.py`:

- `router` — an `APIRouter` exposing `GET /sla-operations/summary`
- `SLAOperationSummary` — a Pydantic model with fields `window_start`, `window_end`, `total_operations`, `sla_met`, `sla_breached`, `sla_compliance_rate`, `average_resolution_seconds`, `p95_resolution_seconds`, `open_breaches`
- `WorkspaceAPIError` — an exception accepting `message`, `status_code`, `code`
- `get_sla_summary_provider` — a FastAPI dependency returning a provider with a `get_summary(**kwargs)` method
- Response envelope shape: `{"success": bool, "data": {...} | None, "error": {"code": str, ...} | None}`

If the router module does not yet exist, a minimal workspace-local stub implementing these names (with `window_end > window_start` validation and `limit` bounded to `1..1000`) is required as a documented prerequisite confined to `app/sla_operations/`.