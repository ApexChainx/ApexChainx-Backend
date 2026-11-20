from datetime import UTC

import pytest

from app.sla_operations.api.error_mapping import (
    WorkspaceAPIError,
    map_workspace_error,
    to_http_exception,
)
from app.sla_operations.api.openapi_metadata import build_openapi_metadata, get_openapi_tags
from app.sla_operations.fixtures.outage_fixtures import get_outage_fixtures
from app.sla_operations.fixtures.sla_metrics_fixtures import get_sla_metrics_fixtures


def test_openapi_metadata_is_fastapi_compatible_and_fresh() -> None:
    tags = get_openapi_tags()
    tags[0]["name"] = "changed"

    assert get_openapi_tags()[0]["name"] == "SLA Operations"
    assert build_openapi_metadata(
        summary="List SLA metrics",
        description="Read-only metrics.",
        tags=["SLA Metrics"],
    ) == {
        "summary": "List SLA metrics",
        "description": "Read-only metrics.",
        "tags": ["SLA Metrics"],
    }


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (ValueError("private details"), 422, "invalid_request"),
        (LookupError("private details"), 404, "not_found"),
        (PermissionError("private details"), 403, "forbidden"),
        (RuntimeError("private details"), 500, "internal_error"),
        (WorkspaceAPIError("conflict", "Conflict.", 409), 409, "conflict"),
    ],
)
def test_workspace_errors_have_stable_public_mapping(
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    status, response = map_workspace_error(error)

    assert status == expected_status
    assert response.code == expected_code
    assert "private details" not in response.message
    http_error = to_http_exception(error)
    assert http_error.status_code == expected_status


def test_workspace_error_rejects_non_error_http_status() -> None:
    with pytest.raises(ValueError, match="between 400 and 599"):
        WorkspaceAPIError("bad_status", "Invalid status.", 200)


def test_sla_metrics_fixtures_are_deterministic_and_isolated() -> None:
    samples = get_sla_metrics_fixtures()
    assert {item["scenario"] for item in samples} == {
        "healthy_month",
        "critical_sla_breach",
        "no_outages",
        "all_outages_violated",
    }
    assert all(item["violated_outages"] <= item["total_outages"] for item in samples)

    samples[0]["total_outages"] = 99
    assert get_sla_metrics_fixtures()[0]["total_outages"] == 4


def test_outage_fixtures_cover_status_severity_and_mutation_edges() -> None:
    samples = get_outage_fixtures()
    assert {item["severity"] for item in samples} == {"critical", "high", "medium", "low"}
    assert {item["status"] for item in samples} == {"active", "resolved"}
    assert all(item["detected_at"].tzinfo == UTC for item in samples)
    assert any(item["sla_status"]["mttr_minutes"] == 0 for item in samples)
    assert any(item["resolved_at"] is None for item in samples)

    samples[0]["affected_services"].append("mutated")
    assert "mutated" not in get_outage_fixtures()[0]["affected_services"]
