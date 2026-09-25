"""Streaming JSON outage import with a typed, reject-or-nothing contract (#547).

Rows are validated against `OutageCreate` — the same schema `POST /outages/import`
uses — so a streamed body and an uploaded file are held to one contract instead of
one path getting ad-hoc checks. Every failure is reported with its row number and
per-field reasons, and the default `atomic` consistency imports nothing unless
every row validates.

This module does not write to the database. It returns the accepted subset
(`valid_row_ids`) for the caller to persist through `OutageRepository`; `db` is
accepted so the wiring does not have to change when that step lands.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.outage_dto import (
    ImportConsistency,
    ImportFieldError,
    ImportRowResult,
    OutageCreate,
)

# Cap on how many failing rows are echoed back. `failed_count` always reports the
# true total, so a truncated list can never be mistaken for a complete one.
MAX_REPORTED_FAILED_ROWS = 50


def _row_error(index: int, raw_row: Any, exc: Exception) -> ImportRowResult:
    """Turn a validation failure into the shared `ImportRowResult` shape."""
    errors: list[ImportFieldError] = []
    if isinstance(exc, ValidationError):
        for detail in exc.errors():
            loc = detail.get("loc") or ()
            errors.append(
                ImportFieldError(
                    field=".".join(str(part) for part in loc) or None,
                    type=detail.get("type"),
                    message=detail.get("msg", str(detail)),
                )
            )
    else:
        errors.append(ImportFieldError(field=None, type=type(exc).__name__, message=str(exc)))

    row_id = raw_row.get("id") if isinstance(raw_row, dict) else None
    return ImportRowResult(row=index, id=row_id, status="error", errors=errors)


def stream_import_outages(
    db: Session,
    raw_body: bytes,
    max_rows: int = 1000,
    chunk_size: int = 100,
    consistency: ImportConsistency = ImportConsistency.atomic,
) -> dict[str, Any]:
    """Stream-parse a JSON body containing outage rows in chunks.

    Args:
        db: Session reserved for the persistence step; unused here.
        raw_body: JSON array of outage objects.
        max_rows: Rows above this count are dropped and reported in `truncated`.
        chunk_size: Rows validated per pass, so peak memory stays flat.
        consistency: `atomic` (default) imports nothing unless every row is valid;
            `partial` accepts the valid subset and reports the rest.

    Returns:
        `imported`, `failed_count`, `failed_rows`, `total_rows`, `truncated`,
        `valid_row_ids` and `consistency`. A body that is not a JSON array returns
        an `error` alongside the same keys, so callers see one shape.
    """
    try:
        rows: list[Any] = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "imported": 0,
            "failed_count": 0,
            "failed_rows": [],
            "total_rows": 0,
            "truncated": 0,
            "valid_row_ids": [],
            "consistency": consistency.value,
            "error": "invalid json",
            "error_detail": str(exc),
        }

    if not isinstance(rows, list):
        return {
            "imported": 0,
            "failed_count": 0,
            "failed_rows": [],
            "total_rows": 0,
            "truncated": 0,
            "valid_row_ids": [],
            "consistency": consistency.value,
            "error": "expected a list",
        }

    total_rows = len(rows)
    truncated = max(0, total_rows - max_rows)
    if truncated:
        # Previously this truncation was silent: rows past the cap vanished while
        # the reported total still described the whole file.
        rows = rows[:max_rows]

    failed: list[ImportRowResult] = []
    valid_row_ids: list[str] = []

    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]
        for offset, row in enumerate(chunk):
            # The row's own position in the payload, not the chunk's start index.
            index = start + offset
            if not isinstance(row, dict):
                failed.append(
                    _row_error(index, row, TypeError(f"expected a JSON object, got {type(row).__name__}"))
                )
                continue
            try:
                payload = OutageCreate(**row)
            except (ValidationError, ValueError, TypeError) as exc:
                failed.append(_row_error(index, row, exc))
                continue
            valid_row_ids.append(payload.id)

    importable = bool(valid_row_ids) and (not failed or consistency == ImportConsistency.partial)
    return {
        "imported": len(valid_row_ids) if importable else 0,
        "failed_count": len(failed),
        "failed_rows": [result.model_dump() for result in failed[:MAX_REPORTED_FAILED_ROWS]],
        "total_rows": total_rows,
        "truncated": truncated,
        # Under `atomic` a single bad row withholds the whole batch, so nothing
        # can be persisted from a body that was partly wrong.
        "valid_row_ids": valid_row_ids if importable else [],
        "consistency": consistency.value,
    }
