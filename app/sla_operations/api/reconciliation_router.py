"""Reconciliation router for the SLA operations workspace.

This module exposes a small, self-contained FastAPI router that accepts SLA
outcomes and payment records, delegates reconciliation to an injectable
callable dependency, and returns a typed response envelope.

Design notes
------------
* The module intentionally avoids importing any core application modules
  (database sessions, ORM models, core routers, etc.). It only depends on
  workspace-local modules under ``app.sla_operations``.
* The reconciliation logic is provided through a FastAPI dependency named
  ``get_reconciliation_service``. Tests (and callers) can override this
  dependency via ``app.dependency_overrides[get_reconciliation_service]``.
  By default it wraps ``reconcile_sla_payments`` from
  ``app.sla_operations.services.reconciliation_checks``.
* Errors raised by the service are mapped to HTTP responses using the
  workspace error-mapping helpers.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.sla_operations.api.error_mapping import (
    WorkspaceAPIError,
    map_workspace_error,
    to_http_exception,
)
from app.sla_operations.api.responses import ResponseEnvelope, success_response
from app.sla_operations.services.reconciliation_checks import reconcile_sla_payments

router = APIRouter(prefix="/reconciliation", tags=["sla-operations"])


# ---------------------------------------------------------------------------
# Request / result models
# ---------------------------------------------------------------------------


class SLAOutcome(BaseModel):
    """A single SLA outcome record to be reconciled."""

    outcome_id: str = Field(..., description="Unique identifier for the SLA outcome.")
    payment_id: str = Field(..., description="Identifier of the related payment.")
    amount: float = Field(..., description="Monetary amount associated with the outcome.")
    currency: str = Field(default="USD", description="Currency code for the amount.")
    status: Optional[str] = Field(
        default=None, description="Optional status label for the outcome."
    )


class PaymentRecord(BaseModel):
    """A single payment record to be reconciled against SLA outcomes."""

    payment_id: str = Field(..., description="Unique identifier for the payment.")
    amount: float = Field(..., description="Monetary amount of the payment.")
    currency: str = Field(default="USD", description="Currency code for the amount.")
    status: Optional[str] = Field(
        default=None, description="Optional status label for the payment."
    )


class ReconciliationRequest(BaseModel):
    """Payload accepted by the reconciliation endpoint."""

    sla_outcomes: List[SLAOutcome] = Field(
        default_factory=list, description="SLA outcomes to reconcile."
    )
    payment_records: List[PaymentRecord] = Field(
        default_factory=list, description="Payment records to reconcile."
    )


class ReconciliationMismatch(BaseModel):
    """A single mismatch detected during reconciliation."""

    payment_id: Optional[str] = None
    outcome_id: Optional[str] = None
    reason: str = ""
    expected: Optional[Any] = None
    actual: Optional[Any] = None


class ReconciliationMismatchReport(BaseModel):
    """Typed result model derived from the service reconciliation report."""

    matched_count: int = 0
    mismatch_count: int = 0
    mismatches: List[ReconciliationMismatch] = Field(default_factory=list)
    is_reconciled: bool = True


# ---------------------------------------------------------------------------
# Injectable reconciliation dependency
# ---------------------------------------------------------------------------


ReconciliationService = Callable[
    [List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Any]
]


def _default_reconciliation_service(
    sla_outcomes: List[Dict[str, Any]],
    payment_records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Thin wrapper around the workspace reconciliation service.

    Kept as a module-level function so it can be referenced and overridden
    deterministically in tests.
    """

    return reconcile_sla_payments(sla_outcomes, payment_records)


def get_reconciliation_service() -> ReconciliationService:
    """FastAPI dependency returning the reconciliation callable.

    Override this dependency in tests via
    ``app.dependency_overrides[get_reconciliation_service]``.
    """

    return _default_reconciliation_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_report(raw_report: Optional[Dict[str, Any]]) -> ReconciliationMismatchReport:
    """Convert a raw service report dict into a typed mismatch report."""

    if not raw_report:
        return ReconciliationMismatchReport()

    raw_mismatches = raw_report.get("mismatches") or []
    mismatches: List[ReconciliationMismatch] = []
    for item in raw_mismatches:
        if isinstance(item, ReconciliationMismatch):
            mismatches.append(item)
        elif isinstance(item, dict):
            mismatches.append(ReconciliationMismatch(**item))
        else:  # pragma: no cover - defensive, unexpected service output
            mismatches.append(ReconciliationMismatch(reason=str(item)))

    matched_count = raw_report.get("matched_count")
    if matched_count is None:
        matched_count = raw_report.get("matched", 0)

    mismatch_count = raw_report.get("mismatch_count")
    if mismatch_count is None:
        mismatch_count = len(mismatches)

    is_reconciled = raw_report.get("is_reconciled")
    if is_reconciled is None:
        is_reconciled = mismatch_count == 0

    return ReconciliationMismatchReport(
        matched_count=int(matched_count or 0),
        mismatch_count=int(mismatch_count or 0),
        mismatches=mismatches,
        is_reconciled=bool(is_reconciled),
    )


def _handle_error(exc: Exception) -> Exception:
    """Map workspace/service errors to HTTP exceptions."""

    if isinstance(exc, WorkspaceAPIError):
        return to_http_exception(exc)
    if isinstance(exc, (LookupError, PermissionError, ValueError)):
        return map_workspace_error(exc)
    return exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/run",
    response_model=ResponseEnvelope[ReconciliationMismatchReport],
    summary="Reconcile SLA outcomes against payment records",
)
def run_reconciliation(
    payload: ReconciliationRequest,
    service: ReconciliationService = Depends(get_reconciliation_service),
) -> ResponseEnvelope[ReconciliationMismatchReport]:
    """Run reconciliation and return a typed mismatch report."""

    sla_outcomes = [outcome.model_dump() for outcome in payload.sla_outcomes]
    payment_records = [record.model_dump() for record in payload.payment_records]

    try:
        raw_report = service(sla_outcomes, payment_records)
    except (WorkspaceAPIError, LookupError, PermissionError, ValueError) as exc:
        raise _handle_error(exc) from exc

    report = _build_report(raw_report)
    return success_response(report)


@router.get(
    "/health",
    response_model=ResponseEnvelope[Dict[str, str]],
    summary="Reconciliation router health check",
)
def reconciliation_health() -> ResponseEnvelope[Dict[str, str]]:
    """Return a simple health payload for the reconciliation router."""

    return success_response({"status": "ok"})