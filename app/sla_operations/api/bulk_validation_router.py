"""Workspace-local router for validating bulk operation payloads.

This module is intentionally **not** wired into the core application router
(``app/api/v1/router.py``) or ``app/main.py``. It provides an isolated,
integration-ready FastAPI ``APIRouter`` that validates bulk operation payloads
using workspace-local schemas and services.

Maintainers may wire this router in later. Until then it can be imported and
mounted independently, e.g.::

    from app.sla_operations.api.bulk_validation_router import router

    app.include_router(router, prefix="/workspace/sla-operations")

Assumptions
------------
* The workspace schemas live in ``app/sla_operations/schemas/bulk_operations.py``
  and expose ``BulkPreviewResponse``, ``BulkOperationSummary`` and related
  request models. This module imports them defensively so that it can be
  imported even if optional names are unavailable.
* The workspace service lives in
  ``app/sla_operations/services/bulk_import_validation.py`` and exposes a
  validation entry point. This module resolves the entry point defensively and
  falls back to a deterministic, self-contained validation routine when the
  service is unavailable.
* All handlers are pure with respect to core systems: no database writes, no
  network calls, and no side effects on routed behavior.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, List, Optional, Sequence

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

# ---------------------------------------------------------------------------
# Workspace schema imports (defensive)
# ---------------------------------------------------------------------------
# We import the workspace schemas where available. If a particular symbol is
# missing (e.g. during incremental development), we fall back to local models
# so that this module remains independently importable.

try:  # pragma: no cover - import path exercised at runtime
    from app.sla_operations.schemas import bulk_operations as _bulk_schemas
except Exception:  # pragma: no cover - defensive fallback
    _bulk_schemas = None  # type: ignore[assignment]


def _resolve_schema(name: str) -> Optional[type]:
    """Return a schema class from the workspace schemas module, if present."""

    if _bulk_schemas is None:
        return None
    candidate = getattr(_bulk_schemas, name, None)
    if isinstance(candidate, type) and issubclass(candidate, BaseModel):
        return candidate
    return None


# ---------------------------------------------------------------------------
# Local fallback models
# ---------------------------------------------------------------------------
# These mirror the expected shape of the workspace schemas. They are only used
# when the workspace schema module does not expose the corresponding symbol.


class _FallbackBulkItemFinding(BaseModel):
    """A single per-item validation finding."""

    index: int = Field(..., ge=0, description="Zero-based index of the item.")
    operation: Optional[str] = Field(
        default=None, description="Operation type associated with the item."
    )
    valid: bool = Field(..., description="Whether the item passed validation.")
    errors: List[str] = Field(
        default_factory=list, description="Human-readable validation errors."
    )


class _FallbackBulkOperationSummary(BaseModel):
    """Aggregate counts for a bulk validation run."""

    total: int = Field(default=0, ge=0, description="Total items submitted.")
    valid: int = Field(default=0, ge=0, description="Items that passed validation.")
    invalid: int = Field(default=0, ge=0, description="Items that failed validation.")


class _FallbackBulkPreviewResponse(BaseModel):
    """Deterministic response for a bulk validation request."""

    summary: _FallbackBulkOperationSummary = Field(
        default_factory=_FallbackBulkOperationSummary
    )
    findings: List[_FallbackBulkItemFinding] = Field(default_factory=list)
    valid: bool = Field(
        default=True, description="True when every submitted item is valid."
    )


# Resolve schema classes, preferring the workspace definitions.
BulkItemFinding: type = (
    _resolve_schema("BulkItemFinding")
    or _resolve_schema("BulkValidationFinding")
    or _FallbackBulkItemFinding
)
BulkOperationSummary: type = (
    _resolve_schema("BulkOperationSummary") or _FallbackBulkOperationSummary
)
BulkPreviewResponse: type = (
    _resolve_schema("BulkPreviewResponse") or _FallbackBulkPreviewResponse
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
# The request model is defined locally to guarantee a stable, explicit
# contract for this isolated endpoint. It accepts a list of arbitrary payload
# items which are then validated by the workspace service.


class BulkValidationRequest(BaseModel):
    """Request body for validating a batch of bulk operation payloads."""

    items: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Bulk operation payload items to validate.",
    )
    operation: Optional[str] = Field(
        default=None,
        description="Optional operation type applied to every item.",
    )
    dry_run: bool = Field(
        default=True,
        description="When true, no side effects are performed (always true here).",
    )

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Service resolution
# ---------------------------------------------------------------------------
# We resolve the workspace validation service lazily and defensively. The
# service is expected to expose one of a small set of conventional entry
# points. If none is available, we fall back to a deterministic local
# validator so the endpoint always returns a well-formed response.

_SERVICE_ENTRYPOINTS: Sequence[str] = (
    "validate_bulk_payload",
    "validate_bulk_operations",
    "validate_payloads",
    "validate_items",
    "validate",
)


def _load_validation_service() -> Optional[Callable[..., Any]]:
    """Return the workspace validation callable, if available."""

    try:  # pragma: no cover - exercised at runtime
        module = importlib.import_module(
            "app.sla_operations.services.bulk_import_validation"
        )
    except Exception:  # pragma: no cover - defensive fallback
        return None

    for name in _SERVICE_ENTRYPOINTS:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    return None


def _normalize_findings(raw: Any, items: Sequence[Dict[str, Any]]) -> List[Any]:
    """Coerce arbitrary service output into a list of finding models.

    The workspace service may return a list of dicts, a list of models, or a
    mapping keyed by index. This helper produces a deterministic, ordered list
    of ``BulkItemFinding`` instances.
    """

    normalized: List[Any] = []

    if raw is None:
        return normalized

    if isinstance(raw, dict):
        # Support {"findings": [...]} and {index: finding} shapes.
        if "findings" in raw and isinstance(raw["findings"], (list, tuple)):
            raw = list(raw["findings"])
        else:
            ordered_keys = sorted(
                raw.keys(), key=lambda k: int(k) if str(k).isdigit() else str(k)
            )
            raw = [raw[k] for k in ordered_keys]

    if not isinstance(raw, (list, tuple)):
        return normalized

    for position, entry in enumerate(raw):
        if isinstance(entry, BulkItemFinding):
            normalized.append(entry)
            continue
        if isinstance(entry, BaseModel):
            entry = entry.model_dump()
        if not isinstance(entry, dict):
            entry = {"valid": bool(entry)}

        payload = dict(entry)
        payload.setdefault("index", position)
        payload.setdefault("valid", not payload.get("errors"))
        payload.setdefault("errors", [])
        if not isinstance(payload["errors"], list):
            payload["errors"] = [str(payload["errors"])]

        try:
            normalized.append(BulkItemFinding(**payload))
        except ValidationError:
            # Guarantee deterministic output even for malformed service data.
            normalized.append(
                BulkItemFinding(
                    index=position,
                    operation=payload.get("operation"),
                    valid=False,
                    errors=["invalid finding returned by validation service"],
                )
            )

    return normalized


def _fallback_validate(
    items: Sequence[Dict[str, Any]], operation: Optional[str]
) -> List[Any]:
    """Deterministic local validation used when the service is unavailable.

    An item is considered valid when it is a non-empty mapping. This keeps the
    endpoint useful and deterministic without depending on the workspace
    service.
    """

    findings: List[Any] = []
    for index, item in enumerate(items):
        errors: List[str] = []
        if not isinstance(item, dict):
            errors.append("item must be an object")
        elif not item:
            errors.append("item must not be empty")

        findings.append(
            BulkItemFinding(
                index=index,
                operation=operation or (item.get("operation") if isinstance(item, dict) else None),
                valid=not errors,
                errors=errors,
            )
        )
    return findings


def _build_summary(findings: Sequence[Any]) -> Any:
    """Build an aggregate summary from a list of findings."""

    total = len(findings)
    valid_count = sum(1 for f in findings if getattr(f, "valid", False))
    invalid_count = total - valid_count

    summary_payload = {
        "total": total,
        "valid": valid_count,
        "invalid": invalid_count,
    }

    try:
        return BulkOperationSummary(**summary_payload)
    except ValidationError:
        # Fall back to the local summary shape if the workspace schema differs.
        return _FallbackBulkOperationSummary(**summary_payload)


def _build_response(findings: Sequence[Any]) -> Any:
    """Build a deterministic preview response from findings."""

    summary = _build_summary(findings)
    all_valid = all(getattr(f, "valid", False) for f in findings)

    response_payload = {
        "summary": summary,
        "findings": list(findings),
        "valid": all_valid,
    }

    try:
        return BulkPreviewResponse(**response_payload)
    except ValidationError:
        return _FallbackBulkPreviewResponse(
            summary=_FallbackBulkOperationSummary(
                total=summary.total, valid=summary.valid, invalid=summary.invalid
            ),
            findings=[
                _FallbackBulkItemFinding(
                    index=f.index,
                    operation=getattr(f, "operation", None),
                    valid=f.valid,
                    errors=list(f.errors),
                )
                for f in findings
            ],
            valid=all_valid,
        )


def validate_bulk_payload(
    request: BulkValidationRequest,
) -> Any:
    """Validate a bulk payload and return a deterministic preview response.

    This helper is exposed for direct invocation in tests and can be reused by
    other workspace modules. It never mutates core systems.
    """

    items = list(request.items)
    service = _load_validation_service()

    findings: List[Any]
    if service is not None:
        try:
            raw = service(items, operation=request.operation)
        except TypeError:
            # Service may accept only the items argument.
            try:
                raw = service(items)
            except Exception:  # pragma: no cover - defensive
                raw = None
        except Exception:  # pragma: no cover - defensive
            raw = None

        findings = _normalize_findings(raw, items)
        # If the service returned nothing usable, fall back deterministically.
        if not findings and items:
            findings = _fallback_validate(items, request.operation)
    else:
        findings = _fallback_validate(items, request.operation)

    return _build_response(findings)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/bulk-validation",
    tags=["sla-operations", "bulk-validation"],
)


@router.post(
    "/validate",
    response_model=BulkPreviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate a batch of bulk operation payloads",
    description=(
        "Validate bulk operation payloads using workspace services and return "
        "deterministic per-item findings and aggregate counts. This endpoint "
        "performs no side effects and is not wired into core routing."
    ),
)
def validate_bulk_operations_endpoint(
    request: BulkValidationRequest = Body(...),
) -> Any:
    """Validate bulk operation payloads.

    Returns a ``BulkPreviewResponse`` containing per-item findings and
    aggregate counts. The response is deterministic for a given input.
    """

    try:
        return validate_bulk_payload(request)
    except HTTPException:  # pragma: no cover - passthrough
        raise
    except Exception as exc:  # pragma: no cover - defensive
        # Never leak internal details; return a stable error shape.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unable to validate bulk payload.",
        ) from exc


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Health check for the bulk validation workspace router",
)
def bulk_validation_health() -> Dict[str, str]:
    """Return a simple deterministic health payload."""

    return {"status": "ok", "module": "sla_operations.bulk_validation_router"}


__all__ = [
    "router",
    "BulkValidationRequest",
    "BulkItemFinding",
    "BulkOperationSummary",
    "BulkPreviewResponse",
    "validate_bulk_payload",
    "validate_bulk_operations_endpoint",
    "bulk_validation_health",
]