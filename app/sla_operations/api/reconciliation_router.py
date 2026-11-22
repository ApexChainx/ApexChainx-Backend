"""Isolated reconciliation router for the SLA operations workspace.

This module exposes a FastAPI ``APIRouter`` for running SLA payment
reconciliation checks. It is intentionally self-contained and is **not**
registered in ``app/api/v1/router.py`` or ``app/main.py``. Maintainers may
wire it in later.

The handlers are side-effect free: they validate inputs, delegate to the
workspace reconciliation service, and return deterministic, typed responses.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.sla_operations.services.reconciliation_checks import (
    reconcile_sla_payments,
)

router = APIRouter(
    prefix="/reconciliation",
    tags=["sla-operations-reconciliation"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ReconciliationCheckInput(BaseModel):
    """A single reconciliation check input record.

    Represents a payment outcome paired with the SLA expectation so the
    reconciliation service can detect mismatches.
    """

    payment_id: str = Field(..., min_length=1, description="Unique payment identifier.")
    outage_id: Optional[str] = Field(
        default=None, description="Associated outage identifier, if any."
    )
    expected_sla_met: bool = Field(
        ..., description="Whether the SLA was expected to be met for this payment."
    )
    actual_sla_met: bool = Field(
        ..., description="Whether the SLA was actually met for this payment."
    )
    expected_amount: Optional[float] = Field(
        default=None, ge=0, description="Expected settlement amount, if applicable."
    )
    actual_amount: Optional[float] = Field(
        default=None, ge=0, description="Actual settled amount, if applicable."
    )

    @field_validator("payment_id")
    @classmethod
    def _strip_payment_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("payment_id must not be blank")
        return stripped


class ReconciliationRunRequest(BaseModel):
    """Request payload for running reconciliation checks."""

    checks: List[ReconciliationCheckInput] = Field(
        default_factory=list,
        description="List of reconciliation check inputs to evaluate.",
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ReconciliationMismatch(BaseModel):
    """A single detected reconciliation mismatch."""

    payment_id: str
    outage_id: Optional[str] = None
    reason: str
    expected_sla_met: Optional[bool] = None
    actual_sla_met: Optional[bool] = None
    expected_amount: Optional[float] = None
    actual_amount: Optional[float] = None


class ReconciliationMismatchReport(BaseModel):
    """Deterministic reconciliation report returned to callers."""

    total_checks: int = Field(..., ge=0)
    matched_count: int = Field(..., ge=0)
    mismatch_count: int = Field(..., ge=0)
    mismatches: List[ReconciliationMismatch] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_mismatch(raw: Any) -> ReconciliationMismatch:
    """Normalize a mismatch record returned by the service into a model.

    The service may return dicts or objects; this helper keeps the router
    tolerant of either representation while producing a typed response.
    """

    if isinstance(raw, ReconciliationMismatch):
        return raw

    if isinstance(raw, dict):
        data = raw
    else:
        data = {
            "payment_id": getattr(raw, "payment_id", ""),
            "outage_id": getattr(raw, "outage_id", None),
            "reason": getattr(raw, "reason", "mismatch"),
            "expected_sla_met": getattr(raw, "expected_sla_met", None),
            "actual_sla_met": getattr(raw, "actual_sla_met", None),
            "expected_amount": getattr(raw, "expected_amount", None),
            "actual_amount": getattr(raw, "actual_amount", None),
        }

    return ReconciliationMismatch(
        payment_id=str(data.get("payment_id", "")),
        outage_id=data.get("outage_id"),
        reason=str(data.get("reason", "mismatch")),
        expected_sla_met=data.get("expected_sla_met"),
        actual_sla_met=data.get("actual_sla_met"),
        expected_amount=data.get("expected_amount"),
        actual_amount=data.get("actual_amount"),
    )


def _build_report(
    total_checks: int, mismatches: List[ReconciliationMismatch]
) -> ReconciliationMismatchReport:
    """Build a deterministic report with sorted mismatches and counts."""

    sorted_mismatches = sorted(
        mismatches,
        key=lambda item: (item.payment_id, item.reason),
    )
    mismatch_count = len(sorted_mismatches)
    matched_count = max(total_checks - mismatch_count, 0)

    return ReconciliationMismatchReport(
        total_checks=total_checks,
        matched_count=matched_count,
        mismatch_count=mismatch_count,
        mismatches=sorted_mismatches,
    )


def run_reconciliation_checks(
    request: ReconciliationRunRequest,
) -> ReconciliationMismatchReport:
    """Run reconciliation checks and return a deterministic typed report.

    This helper is exposed for direct invocation in tests and is used by the
    HTTP handler below.
    """

    checks = request.checks

    if not checks:
        return _build_report(total_checks=0, mismatches=[])

    payload = [check.model_dump() for check in checks]

    try:
        raw_result = reconcile_sla_payments(payload)
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Reconciliation service failed to process checks.",
        ) from exc

    raw_mismatches: List[Any]
    if raw_result is None:
        raw_mismatches = []
    elif isinstance(raw_result, dict):
        raw_mismatches = list(
            raw_result.get("mismatches")
            or raw_result.get("mismatch_report")
            or []
        )
    elif isinstance(raw_result, (list, tuple)):
        raw_mismatches = list(raw_result)
    else:
        raw_mismatches = list(getattr(raw_result, "mismatches", []) or [])

    mismatches = [_coerce_mismatch(item) for item in raw_mismatches]

    return _build_report(total_checks=len(checks), mismatches=mismatches)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/checks/run",
    response_model=ReconciliationMismatchReport,
    status_code=status.HTTP_200_OK,
    summary="Run SLA payment reconciliation checks",
    description=(
        "Validate the supplied reconciliation check inputs and return a "
        "deterministic mismatch report. This endpoint is side-effect free."
    ),
)
def run_checks(request: ReconciliationRunRequest) -> ReconciliationMismatchReport:
    """Run reconciliation checks over the provided inputs."""

    return run_reconciliation_checks(request)


@router.get(
    "/health",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Reconciliation router health check",
)
def health() -> dict:
    """Simple deterministic health probe for the isolated router."""

    return {"status": "ok", "module": "sla_operations.reconciliation"}


__all__ = [
    "router",
    "ReconciliationCheckInput",
    "ReconciliationRunRequest",
    "ReconciliationMismatch",
    "ReconciliationMismatchReport",
    "run_reconciliation_checks",
    "run_checks",
    "health",
]