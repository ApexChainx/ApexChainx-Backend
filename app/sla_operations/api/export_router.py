"""Workspace-local export router for the SLA operations package.

This module provides an isolated FastAPI ``APIRouter`` for preparing export
payloads and metadata. It is intentionally *not* wired into the core
``app/api/v1/router.py`` or ``app/main.py``; maintainers may integrate it later.

The module is importable standalone and has no side effects on core systems.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.sla_operations.api.error_mapping import to_http_exception
from app.sla_operations.api.responses import success_response
from app.sla_operations.schemas.exports import (
    ExportArtifact,
    ExportFormat,
    ExportRequest,
    ExportResource,
    ExportResponse,
    ExportStatus,
)
from app.sla_operations.services.export_preparation import prepare_export_dataset

router = APIRouter(tags=["sla-operations-export"])

# Default artifact lifetime for prepared exports.
DEFAULT_EXPORT_TTL = timedelta(hours=1)


class ExportFormatsResponse(BaseModel):
    """Response model describing the supported export formats."""

    formats: List[ExportFormat] = Field(
        ..., description="Supported export formats with metadata."
    )


def _derive_column_order(
    fields: Optional[Sequence[str]],
    records: Sequence[Dict[str, Any]],
) -> List[str]:
    """Derive a deterministic column order for the export.

    If ``fields`` is provided, its order is preserved (deduplicated). Otherwise
    the order is derived from the first appearance of each key across records.
    """
    if fields:
        seen: Dict[str, None] = {}
        for name in fields:
            if name not in seen:
                seen[name] = None
        return list(seen.keys())

    ordered: Dict[str, None] = {}
    for record in records:
        for key in record.keys():
            if key not in ordered:
                ordered[key] = None
    return list(ordered.keys())


def _apply_filters(
    records: Sequence[Dict[str, Any]],
    filters: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply exact-match filtering to records.

    A record is retained only when every filter key exists in the record and
    its value equals the filter value exactly.
    """
    if not filters:
        return [dict(record) for record in records]

    filtered: List[Dict[str, Any]] = []
    for record in records:
        if all(
            key in record and record[key] == value
            for key, value in filters.items()
        ):
            filtered.append(dict(record))
    return filtered


def _project_records(
    records: Sequence[Dict[str, Any]],
    columns: Sequence[str],
) -> List[Dict[str, Any]]:
    """Project records onto the given columns, preserving column order."""
    return [{column: record.get(column) for column in columns} for record in records]


def _render_bytes(
    records: Sequence[Dict[str, Any]],
    columns: Sequence[str],
    export_format: ExportFormat,
) -> bytes:
    """Render the export dataset to bytes for the requested format.

    Rendering is deterministic: records are serialized in the order provided and
    keys follow the derived column order.
    """
    projected = _project_records(records, columns)

    if export_format == ExportFormat.CSV:
        header = ",".join(columns)
        lines = [header]
        for record in projected:
            row = ",".join(_stringify(record.get(column)) for column in columns)
            lines.append(row)
        return ("\n".join(lines) + "\n").encode("utf-8")

    if export_format == ExportFormat.JSON:
        return json.dumps(
            projected, sort_keys=False, separators=(",", ":"), default=str
        ).encode("utf-8")

    # Fallback: treat unknown formats as JSON for deterministic behavior.
    return json.dumps(
        projected, sort_keys=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def _stringify(value: Any) -> str:
    """Convert a value to a CSV-safe string representation."""
    if value is None:
        return ""
    text = str(value)
    if any(char in text for char in (",", '"', "\n", "\r")):
        escaped = text.replace('"', '""')
        return f'"{escaped}"'
    return text


def _checksum_sha256(payload: bytes) -> str:
    """Compute the SHA-256 hex digest of the rendered payload."""
    return hashlib.sha256(payload).hexdigest()


def _build_filename(resource: ExportResource, export_format: ExportFormat) -> str:
    """Build a deterministic filename for the export artifact."""
    return f"{resource.value}_export.{export_format.value}"


def _build_storage_key(
    resource: ExportResource,
    export_format: ExportFormat,
    checksum: str,
) -> str:
    """Build a deterministic storage key for the export artifact."""
    return f"sla_operations/exports/{resource.value}/{checksum}.{export_format.value}"


def build_export_dataset(request: ExportRequest) -> Dict[str, Any]:
    """Build the export dataset for a request.

    Delegates to :func:`prepare_export_dataset` to obtain the raw records, then
    applies exact-match filters and derives a deterministic column order.

    Returns a dictionary with ``columns`` and ``records`` keys.
    """
    dataset = prepare_export_dataset(request)

    if isinstance(dataset, dict):
        raw_records = dataset.get("records", [])
    else:
        raw_records = list(dataset)

    records = [dict(record) for record in raw_records]
    records = _apply_filters(records, getattr(request, "filters", None))
    columns = _derive_column_order(getattr(request, "fields", None), records)

    return {"columns": columns, "records": records}


def prepare_export_response(request: ExportRequest) -> ExportResponse:
    """Prepare a deterministic :class:`ExportResponse` for a request."""
    dataset = build_export_dataset(request)
    columns: List[str] = dataset["columns"]
    records: List[Dict[str, Any]] = dataset["records"]

    payload = _render_bytes(records, columns, request.format)
    checksum = _checksum_sha256(payload)

    now = datetime.now(timezone.utc)
    generated_at = now
    expires_at = now + DEFAULT_EXPORT_TTL

    artifact = ExportArtifact(
        filename=_build_filename(request.resource, request.format),
        content_type=request.format.content_type,
        size_bytes=len(payload),
        checksum_sha256=checksum,
        storage_key=_build_storage_key(request.resource, request.format, checksum),
        generated_at=generated_at,
        expires_at=expires_at,
    )

    return ExportResponse(
        status=ExportStatus.READY,
        resource=request.resource,
        format=request.format,
        artifact=artifact,
        record_count=len(records),
        columns=columns,
    )


@router.get(
    "/exports/formats",
    response_model=ExportFormatsResponse,
    status_code=status.HTTP_200_OK,
    summary="List supported export formats",
)
def list_export_formats() -> ExportFormatsResponse:
    """Return the supported export formats with their metadata."""
    return ExportFormatsResponse(formats=list(ExportFormat))


@router.post(
    "/exports/prepare",
    status_code=status.HTTP_200_OK,
    summary="Prepare an export payload and metadata",
)
def prepare_export(request: ExportRequest) -> Any:
    """Validate an :class:`ExportRequest` and prepare export metadata.

    Returns a success envelope wrapping the prepared :class:`ExportResponse`.
    Errors are translated into HTTP exceptions via ``to_http_exception``.
    """
    try:
        response = prepare_export_response(request)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP error below
        raise to_http_exception(exc) from exc

    return success_response(response)