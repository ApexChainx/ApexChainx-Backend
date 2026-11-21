from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, TypedDict

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()

EXPORT_FILENAME = "sla_operations_export.csv"
EXPORT_CONTENT_TYPE = "text/csv"

# Small, deterministic in-module sample dataset used for workspace-local exports.
SAMPLE_EXPORT_ROWS: List[Dict[str, Any]] = [
    {
        "sla_id": "SLA-001",
        "service": "payments",
        "status": "met",
        "target_minutes": 60,
        "actual_minutes": 42,
    },
    {
        "sla_id": "SLA-002",
        "service": "settlement",
        "status": "breached",
        "target_minutes": 120,
        "actual_minutes": 155,
    },
    {
        "sla_id": "SLA-003",
        "service": "webhooks",
        "status": "met",
        "target_minutes": 30,
        "actual_minutes": 18,
    },
]

EXPORT_FIELDNAMES: List[str] = [
    "sla_id",
    "service",
    "status",
    "target_minutes",
    "actual_minutes",
]


class ExportMetadata(TypedDict):
    filename: str
    content_type: str
    row_count: int
    generated_at: str


def prepare_export_metadata(
    rows: List[Dict[str, Any]] | None = None,
    *,
    filename: str = EXPORT_FILENAME,
    content_type: str = EXPORT_CONTENT_TYPE,
    generated_at: datetime | None = None,
) -> ExportMetadata:
    """Build typed metadata describing a CSV export.

    Args:
        rows: Rows included in the export. Defaults to the sample dataset.
        filename: Name of the exported file.
        content_type: MIME type of the exported content.
        generated_at: Timestamp of generation. Defaults to the current UTC time.

    Returns:
        A typed dict with filename, content type, row count, and generated timestamp.
    """
    export_rows = SAMPLE_EXPORT_ROWS if rows is None else rows
    timestamp = generated_at if generated_at is not None else datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    return ExportMetadata(
        filename=filename,
        content_type=content_type,
        row_count=len(export_rows),
        generated_at=timestamp.isoformat(),
    )


def _render_csv(rows: List[Dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


@router.get("/export")
def export_sla_operations() -> Response:
    """Return the SLA operations sample dataset as a CSV download."""
    metadata = prepare_export_metadata(SAMPLE_EXPORT_ROWS)
    body = _render_csv(SAMPLE_EXPORT_ROWS)
    headers = {
        "Content-Disposition": f'attachment; filename="{metadata["filename"]}"',
        "X-Export-Row-Count": str(metadata["row_count"]),
        "X-Export-Generated-At": metadata["generated_at"],
    }
    return Response(
        content=body,
        media_type=metadata["content_type"],
        headers=headers,
    )