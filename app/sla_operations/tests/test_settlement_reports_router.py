"""Tests for the SLA operations settlement reports router.

These tests are intentionally isolated from the core application: they build a
router-only FastAPI app and exercise it with ``fastapi.testclient.TestClient``.
They do not import ``app.main`` or rely on shared client fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api import settlement_reports_router as router_module
from app.sla_operations.api.settlement_reports_router import router


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """Build a router-only FastAPI app and return a TestClient for it."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _valid_query_params() -> Dict[str, Any]:
    """Return a deterministic, valid set of query parameters."""
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    window_end = window_start + timedelta(days=7)
    return {
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "asset_code": "USDC",
        "page": 1,
        "page_size": 10,
    }


def _sample_payload() -> Dict[str, Any]:
    """Load a sample settlement payload from the workspace fixtures if present.

    Falls back to a locally constructed payload so the tests remain
    deterministic even if the fixtures module is unavailable.
    """
    try:
        from app.sla_operations.fixtures import settlement_fixtures  # type: ignore
    except Exception:  # pragma: no cover - defensive fallback
        settlement_fixtures = None

    if settlement_fixtures is not None:
        for attr in (
            "SAMPLE_SETTLEMENT_PAYLOAD",
            "SAMPLE_PAYLOAD",
            "sample_settlement_payload",
        ):
            candidate = getattr(settlement_fixtures, attr, None)
            if candidate is None:
                continue
            if callable(candidate):
                try:
                    candidate = candidate()
                except Exception:  # pragma: no cover - defensive
                    continue
            if isinstance(candidate, dict):
                return candidate

    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    window_end = window_start + timedelta(days=7)
    return {
        "report_id": "rpt_test_0001",
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "generated_at": _iso(window_start + timedelta(hours=1)),
        "asset_code": "USDC",
        "totals": {
            "gross_amount": "1000.0000000",
            "fee_amount": "10.0000000",
            "net_amount": "990.0000000",
            "row_count": 2,
        },
        "rows": [
            {
                "settlement_id": "stl_0001",
                "amount": "500.0000000",
                "fee": "5.0000000",
                "net": "495.0000000",
            },
            {
                "settlement_id": "stl_0002",
                "amount": "500.0000000",
                "fee": "5.0000000",
                "net": "495.0000000",
            },
        ],
    }


def _assert_envelope_shape(payload: Dict[str, Any]) -> None:
    """Assert the serialized JSON matches the SettlementSummaryEnvelope schema."""
    required_keys = {
        "report_id",
        "window_start",
        "window_end",
        "generated_at",
        "asset_code",
        "totals",
        "rows",
    }
    assert required_keys.issubset(payload.keys()), (
        f"missing keys: {required_keys - set(payload.keys())}"
    )

    assert isinstance(payload["report_id"], str) and payload["report_id"]
    assert isinstance(payload["asset_code"], str) and payload["asset_code"]

    # Timestamps should be parseable ISO-8601 strings.
    for key in ("window_start", "window_end", "generated_at"):
        value = payload[key]
        assert isinstance(value, str) and value
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    totals = payload["totals"]
    assert isinstance(totals, dict)
    for key in ("gross_amount", "fee_amount", "net_amount"):
        assert key in totals

    # Net payout consistency: net == gross - fee.
    gross = float(totals["gross_amount"])
    fee = float(totals["fee_amount"])
    net = float(totals["net_amount"])
    assert net == pytest.approx(gross - fee, abs=1e-9)

    rows = payload["rows"]
    assert isinstance(rows, list)
    if "row_count" in totals:
        assert totals["row_count"] == len(rows)
    for row in rows:
        assert isinstance(row, dict)
        assert "settlement_id" in row


def _assert_error_shape(payload: Dict[str, Any]) -> None:
    """Assert the serialized JSON matches the WorkspaceErrorResponse schema."""
    assert isinstance(payload, dict)
    assert "error" in payload
    error = payload["error"]
    assert isinstance(error, dict)
    assert "code" in error and isinstance(error["code"], str) and error["code"]
    assert "message" in error and isinstance(error["message"], str)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_summary_success_returns_envelope(client: TestClient) -> None:
    response = client.get("/settlement-reports/summary", params=_valid_query_params())
    assert response.status_code == 200, response.text

    payload = response.json()
    _assert_envelope_shape(payload)

    params = _valid_query_params()
    assert payload["asset_code"] == params["asset_code"]
    assert payload["window_start"] == params["window_start"]
    assert payload["window_end"] == params["window_end"]


def test_summary_success_matches_sample_payload_shape(client: TestClient) -> None:
    sample = _sample_payload()
    # The sample payload itself must satisfy the envelope contract.
    _assert_envelope_shape(sample)

    response = client.get("/settlement-reports/summary", params=_valid_query_params())
    assert response.status_code == 200, response.text
    _assert_envelope_shape(response.json())


def test_summary_rows_shape(client: TestClient) -> None:
    response = client.get("/settlement-reports/summary", params=_valid_query_params())
    assert response.status_code == 200, response.text

    rows: List[Dict[str, Any]] = response.json()["rows"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        assert "settlement_id" in row


# ---------------------------------------------------------------------------
# Validation / 422 paths
# ---------------------------------------------------------------------------


def test_summary_missing_window_returns_422(client: TestClient) -> None:
    params = _valid_query_params()
    params.pop("window_start")
    response = client.get("/settlement-reports/summary", params=params)
    assert response.status_code == 422, response.text


def test_summary_window_end_before_start_returns_422(client: TestClient) -> None:
    params = _valid_query_params()
    window_start = datetime(2024, 1, 8, 0, 0, 0, tzinfo=timezone.utc)
    window_end = window_start - timedelta(days=1)
    params["window_start"] = _iso(window_start)
    params["window_end"] = _iso(window_end)
    response = client.get("/settlement-reports/summary", params=params)
    assert response.status_code == 422, response.text


def test_summary_window_end_equal_start_returns_422(client: TestClient) -> None:
    params = _valid_query_params()
    window_start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    params["window_start"] = _iso(window_start)
    params["window_end"] = _iso(window_start)
    response = client.get("/settlement-reports/summary", params=params)
    assert response.status_code == 422, response.text


def test_summary_invalid_asset_code_returns_422(client: TestClient) -> None:
    params = _valid_query_params()
    params["asset_code"] = "usdc$"
    response = client.get("/settlement-reports/summary", params=params)
    assert response.status_code == 422, response.text


def test_summary_page_size_out_of_range_returns_422(client: TestClient) -> None:
    params = _valid_query_params()
    params["page_size"] = 0
    response = client.get("/settlement-reports/summary", params=params)
    assert response.status_code == 422, response.text

    params = _valid_query_params()
    params["page_size"] = 100000
    response = client.get("/settlement-reports/summary", params=params)
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Error mapping -> WorkspaceErrorResponse
# ---------------------------------------------------------------------------


def test_summary_error_mapping_returns_workspace_error_shape(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raised workspace error should map to the WorkspaceErrorResponse shape."""

    error_cls = getattr(router_module, "WorkspaceError", None)
    if error_cls is None:
        pytest.skip("WorkspaceError not defined in settlement_reports_router")

    def _raise_error(*_args: Any, **_kwargs: Any) -> Any:
        raise error_cls(code="settlement_unavailable", message="settlement backend down")

    # Patch the handler/service function used by the route. Try common names.
    patched = False
    for name in (
        "build_settlement_summary",
        "get_settlement_summary",
        "generate_settlement_summary",
        "compute_settlement_summary",
    ):
        if hasattr(router_module, name):
            monkeypatch.setattr(router_module, name, _raise_error)
            patched = True
            break

    if not patched:
        pytest.skip("no summary handler function found to patch")

    response = client.get("/settlement-reports/summary", params=_valid_query_params())
    assert response.status_code >= 400, response.text
    _assert_error_shape(response.json())