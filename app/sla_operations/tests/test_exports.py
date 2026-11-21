"""Unit tests for export request, status, and artifact schemas."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.exports import (
    ExportArtifact,
    ExportFormat,
    ExportRequest,
    ExportResource,
    ExportResponse,
    ExportStatus,
)

CHECKSUM = "a" * 64


def _artifact_payload(**overrides):
    payload = {
        "filename": "outages-2026.csv",
        "content_type": "text/csv",
        "size_bytes": 128,
        "checksum_sha256": CHECKSUM,
        "storage_key": "exports/2026/outages-2026.csv",
        "generated_at": "2026-01-01T10:00:00Z",
    }
    payload.update(overrides)
    return payload


def _response_payload(**overrides):
    payload = {
        "id": "exp-1",
        "resource": "outages",
        "format": "csv",
        "status": "pending",
        "requested_at": "2026-01-01T09:00:00Z",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("export_format", "content_type"),
    [
        (ExportFormat.csv, "text/csv"),
        (ExportFormat.json, "application/json"),
        (ExportFormat.xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (ExportFormat.pdf, "application/pdf"),
    ],
)
def test_export_formats_have_content_types(export_format, content_type):
    assert export_format.content_type == content_type


def test_export_request_defaults_and_filters():
    request = ExportRequest(resource="outages", format="csv")
    assert request.filters == {}
    assert request.fields is None
    assert request.include_metadata is False
    assert request.resource is ExportResource.outages
    assert request.format is ExportFormat.csv


def test_export_request_normalizes_dates_and_accepts_projection():
    request = ExportRequest(
        resource="timeline_events",
        format="json",
        filters={"outage_id": "outage-1"},
        fields=["id", "occurred_at"],
        date_from="2026-01-01T12:00:00+02:00",
        date_to="2026-01-02T00:00:00Z",
        requested_by="operator@example.com",
    )
    assert request.date_from == datetime(2026, 1, 1, 10, tzinfo=UTC)
    assert request.fields == ["id", "occurred_at"]
    assert request.requested_by == "operator@example.com"


@pytest.mark.parametrize(
    "overrides",
    [
        {"format": "docx"},
        {"resource": "unknown"},
        {"fields": []},
        {"fields": ["id", " "]},
        {"filters": {"": "value"}},
        {"date_to": "2026-01-01T00:00:00"},
        {"date_from": "2026-01-02T00:00:00Z", "date_to": "2026-01-01T00:00:00Z"},
        {"requested_by": ""},
    ],
)
def test_export_request_rejects_invalid_payloads(overrides):
    payload = {"resource": "outages", "format": "csv"}
    payload.update(overrides)
    with pytest.raises(ValidationError):
        ExportRequest(**payload)


def test_export_artifact_validates_checksum_and_expiry():
    artifact = ExportArtifact(**_artifact_payload(expires_at="2026-01-03T00:00:00Z"))
    assert artifact.size_bytes == 128
    assert artifact.expires_at == datetime(2026, 1, 3, tzinfo=UTC)
    with pytest.raises(ValidationError, match="checksum_sha256"):
        ExportArtifact(**_artifact_payload(checksum_sha256="not-a-digest"))
    with pytest.raises(ValidationError):
        ExportArtifact(**_artifact_payload(size_bytes=-1))
    with pytest.raises(ValidationError, match="expires_at cannot be before"):
        ExportArtifact(**_artifact_payload(expires_at="2025-12-31T00:00:00Z"))
    with pytest.raises(ValidationError, match="timezone-aware"):
        ExportArtifact(**_artifact_payload(generated_at="2026-01-01T00:00:00"))


def test_export_response_enforces_terminal_state_requirements():
    pending = ExportResponse(**_response_payload())
    assert pending.status is ExportStatus.pending
    assert pending.artifact is None

    completed = ExportResponse(
        **_response_payload(
            status="completed",
            completed_at="2026-01-01T09:05:00Z",
            artifact=_artifact_payload(),
        )
    )
    assert completed.artifact is not None
    assert completed.artifact.checksum_sha256 == CHECKSUM

    failed = ExportResponse(**_response_payload(status="failed", error="source query timed out"))
    assert failed.error == "source query timed out"

    with pytest.raises(ValidationError, match="completed export must include"):
        ExportResponse(**_response_payload(status="completed"))
    with pytest.raises(ValidationError, match="failed export must include"):
        ExportResponse(**_response_payload(status="failed"))
    with pytest.raises(ValidationError, match="completed_at cannot be before"):
        ExportResponse(
            **_response_payload(status="completed", completed_at="2025-12-31T00:00:00Z", artifact=_artifact_payload())
        )


def test_export_response_reads_attributes():
    raw = SimpleNamespace(**_response_payload(artifact=None, completed_at=None, error=None))
    assert ExportResponse.model_validate(raw).id == "exp-1"
