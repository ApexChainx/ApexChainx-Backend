"""Tests for the SLA operations bulk validation router.

These tests exercise the isolated router module without touching the core
application router. A standalone FastAPI app is constructed per test so the
module can be validated independently.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sla_operations.api.bulk_validation_router import (
    BulkValidationRequest,
    BulkValidationResponse,
    BulkValidationItem,
    BulkValidationFinding,
    router,
    validate_bulk_payload,
)


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _sample_valid_item() -> dict:
    return {
        "operation_id": "op-1",
        "operation_type": "sla_breach_acknowledge",
        "payload": {"sla_id": "sla-1", "acknowledged_by": "user-1"},
    }


def _sample_invalid_item() -> dict:
    return {
        "operation_id": "op-2",
        "operation_type": "sla_breach_acknowledge",
        "payload": {},
    }


def test_router_is_importable_without_core_router():
    # The module must be importable on its own; the router object should exist.
    assert router is not None
    assert hasattr(router, "routes")


def test_validate_bulk_payload_valid_items():
    request = BulkValidationRequest(
        items=[
            BulkValidationItem(**_sample_valid_item()),
        ]
    )
    response = validate_bulk_payload(request)
    assert isinstance(response, BulkValidationResponse)
    assert response.valid is True
    assert response.total == 1
    assert response.valid_count == 1
    assert response.invalid_count == 0
    assert response.results[0].valid is True
    assert response.results[0].findings == []


def test_validate_bulk_payload_empty_list():
    request = BulkValidationRequest(items=[])
    response = validate_bulk_payload(request)
    assert isinstance(response, BulkValidationResponse)
    assert response.valid is True
    assert response.total == 0
    assert response.valid_count == 0
    assert response.invalid_count == 0
    assert response.results == []


def test_validate_bulk_payload_invalid_items_produce_findings():
    request = BulkValidationRequest(
        items=[
            BulkValidationItem(**_sample_valid_item()),
            BulkValidationItem(**_sample_invalid_item()),
        ]
    )
    response = validate_bulk_payload(request)
    assert response.valid is False
    assert response.total == 2
    assert response.valid_count == 1
    assert response.invalid_count == 1

    valid_result, invalid_result = response.results
    assert valid_result.valid is True
    assert valid_result.findings == []

    assert invalid_result.valid is False
    assert invalid_result.operation_id == "op-2"
    assert len(invalid_result.findings) >= 1
    for finding in invalid_result.findings:
        assert isinstance(finding, BulkValidationFinding)
        assert finding.code
        assert finding.message


def test_validate_bulk_payload_deterministic_findings():
    request = BulkValidationRequest(
        items=[BulkValidationItem(**_sample_invalid_item())]
    )
    first = validate_bulk_payload(request)
    second = validate_bulk_payload(request)
    assert first.model_dump() == second.model_dump()


def test_endpoint_valid_payload():
    client = _build_client()
    response = client.post(
        "/sla-operations/bulk-validation/validate",
        json={"items": [_sample_valid_item()]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["total"] == 1
    assert body["valid_count"] == 1
    assert body["invalid_count"] == 0
    assert body["results"][0]["valid"] is True


def test_endpoint_empty_list():
    client = _build_client()
    response = client.post(
        "/sla-operations/bulk-validation/validate",
        json={"items": []},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["total"] == 0
    assert body["results"] == []


def test_endpoint_invalid_items():
    client = _build_client()
    response = client.post(
        "/sla-operations/bulk-validation/validate",
        json={"items": [_sample_valid_item(), _sample_invalid_item()]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["total"] == 2
    assert body["valid_count"] == 1
    assert body["invalid_count"] == 1
    assert body["results"][1]["valid"] is False
    assert len(body["results"][1]["findings"]) >= 1


def test_endpoint_rejects_malformed_request():
    client = _build_client()
    response = client.post(
        "/sla-operations/bulk-validation/validate",
        json={"items": "not-a-list"},
    )
    assert response.status_code == 422


def test_endpoint_rejects_missing_items():
    client = _build_client()
    response = client.post(
        "/sla-operations/bulk-validation/validate",
        json={},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "operation_type",
    ["sla_breach_acknowledge", "sla_breach_escalate"],
)
def test_endpoint_accepts_known_operation_types(operation_type: str):
    client = _build_client()
    item = {
        "operation_id": "op-x",
        "operation_type": operation_type,
        "payload": {"sla_id": "sla-1"},
    }
    response = client.post(
        "/sla-operations/bulk-validation/validate",
        json={"items": [item]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["results"][0]["operation_id"] == "op-x"