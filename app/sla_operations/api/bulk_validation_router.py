"""Bulk validation router for the SLA operations workspace.

This module exposes a small, self-contained FastAPI router that allows
clients to preview bulk SLA operation payloads without persisting anything.
The endpoint is intentionally pure and deterministic: it validates the
incoming rows using the workspace-local validation service and returns a
structured preview response.

The router is isolated from the core application routers and does not touch
any persistence layer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from fastapi import APIRouter, Body, HTTPException, status

from app.sla_operations.api.error_mapping import map_validation_error
from app.sla_operations.schemas.bulk_operations import (
    BulkOperationSummary,
    BulkPreviewItem,
    BulkPreviewResponse,
)
from app.sla_operations.services import bulk_import_validation

router = APIRouter(
    prefix="/bulk-validation",
    tags=["SLA Operations Bulk Validation"],
)


def _coerce_rows(payload: Any) -> List[Mapping[str, Any]]:
    """Normalize the incoming payload into a list of row mappings.

    Accepts either a bare list of mappings or a mapping with a ``rows`` key
    containing the list. Raises ``ValueError`` for anything else so that the
    caller can map it to a 422 response.
    """

    if isinstance(payload, Mapping):
        if "rows" not in payload:
            raise ValueError("Payload must contain a 'rows' field.")
        rows = payload["rows"]
    else:
        rows = payload

    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("Payload rows must be a list of row mappings.")

    normalized: List[Mapping[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"Row at index {index} must be a mapping.")
        normalized.append(row)

    return normalized


def _build_preview_item(
    index: int,
    row: Mapping[str, Any],
    result: Any,
) -> BulkPreviewItem:
    """Build a single preview item from a validation result.

    The validation service is expected to return an object exposing
    ``is_valid``, ``errors`` and optionally ``normalized`` attributes. To keep
    the router resilient to minor shape differences we fall back to dict-style
    access when attribute access is unavailable.
    """

    def _get(name: str, default: Any = None) -> Any:
        if hasattr(result, name):
            return getattr(result, name)
        if isinstance(result, Mapping):
            return result.get(name, default)
        return default

    is_valid = bool(_get("is_valid", False))
    errors = list(_get("errors", []) or [])
    normalized = _get("normalized", None)

    return BulkPreviewItem(
        row_index=index,
        is_valid=is_valid,
        errors=errors,
        normalized=normalized,
    )


def _build_summary(items: Sequence[BulkPreviewItem]) -> BulkOperationSummary:
    """Aggregate preview items into a summary."""

    total = len(items)
    valid = sum(1 for item in items if item.is_valid)
    invalid = total - valid

    return BulkOperationSummary(
        total_rows=total,
        valid_rows=valid,
        invalid_rows=invalid,
    )


@router.post(
    "/preview",
    response_model=BulkPreviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview bulk SLA operation validation",
    description=(
        "Validate a batch of raw SLA operation rows and return a per-row "
        "preview without persisting anything."
    ),
)
def preview_bulk_validation(
    payload: Any = Body(
        ...,
        description=(
            "Either a list of row mappings or an object with a 'rows' field "
            "containing the list of row mappings."
        ),
    ),
) -> BulkPreviewResponse:
    """Validate bulk rows and return a deterministic preview response."""

    try:
        rows = _coerce_rows(payload)
    except ValueError as exc:
        raise map_validation_error(exc) from exc

    if not rows:
        raise map_validation_error(ValueError("Payload contains no rows."))

    try:
        results = bulk_import_validation.validate_rows(rows)
    except ValueError as exc:
        raise map_validation_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to a 500 response
        raise map_validation_error(exc) from exc

    if results is None:
        results = []

    results_list = list(results)
    if len(results_list) != len(rows):
        raise map_validation_error(
            ValueError(
                "Validation service returned an unexpected number of results."
            )
        )

    items: List[BulkPreviewItem] = [
        _build_preview_item(index, row, result)
        for index, (row, result) in enumerate(zip(rows, results_list))
    ]

    summary = _build_summary(items)

    return BulkPreviewResponse(items=items, summary=summary)


__all__ = ["router", "preview_bulk_validation"]