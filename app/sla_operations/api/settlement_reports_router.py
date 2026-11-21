"""Settlement reports router for the SLA operations workspace.

This module exposes a small, self-contained FastAPI router that serves
deterministic, in-memory settlement report summaries. It is intentionally
isolated from the core application routers and does not import ``app.main``
or any maintainer-owned modules. It reuses the workspace-local schemas and
response/error helpers so that behavior stays consistent with the rest of the
SLA operations workspace.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from app.sla_operations.api.error_mapping import to_http_exception
from app.sla_operations.api.responses import success_response
from app.sla_operations.schemas.settlement_reports import (
    SettlementReportFilter,
    SettlementReportRow,
    SettlementSummaryEnvelope,
)

router = APIRouter(
    prefix="/settlement-reports",
    tags=["sla-operations", "settlement-reports"],
)


# ---------------------------------------------------------------------------
# Deterministic in-memory data source
# ---------------------------------------------------------------------------

# A fixed reference "now" keeps generated timestamps deterministic across runs.
_REFERENCE_NOW = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

# Static, deterministic rows used to build settlement summaries. Each row is
# keyed by a stable identifier so filtering produces predictable output.
_IN_MEMORY_ROWS: List[SettlementReportRow] = [
    SettlementReportRow(
        settlement_id="stl-0001",
        account_id="acct-alpha",
        asset_code="XLM",
        amount=Decimal("100.0000000"),
        status="settled",
        created_at=_REFERENCE_NOW,
    ),
    SettlementReportRow(
        settlement_id="stl-0002",
        account_id="acct-alpha",
        asset_code="USDC",
        amount=Decimal("250.5000000"),
        status="pending",
        created_at=_REFERENCE_NOW,
    ),
    SettlementReportRow(
        settlement_id="stl-0003",
        account_id="acct-beta",
        asset_code="XLM",
        amount=Decimal("42.0000000"),
        status="settled",
        created_at=_REFERENCE_NOW,
    ),
    SettlementReportRow(
        settlement_id="stl-0004",
        account_id="acct-beta",
        asset_code="USDC",
        amount=Decimal("17.2500000"),
        status="failed",
        created_at=_REFERENCE_NOW,
    ),
]


def _matches_filter(row: SettlementReportRow, filters: SettlementReportFilter) -> bool:
    """Return ``True`` when ``row`` satisfies every provided filter field."""

    if filters.account_id is not None and row.account_id != filters.account_id:
        return False
    if filters.asset_code is not None and row.asset_code != filters.asset_code:
        return False
    if filters.status is not None and row.status != filters.status:
        return False
    if filters.settlement_id is not None and row.settlement_id != filters.settlement_id:
        return False
    return True


def _build_summary(filters: SettlementReportFilter) -> SettlementSummaryEnvelope:
    """Build a deterministic summary envelope for the given filters."""

    matched = [row for row in _IN_MEMORY_ROWS if _matches_filter(row, filters)]

    total_amount = sum((row.amount for row in matched), Decimal("0"))
    settled_count = sum(1 for row in matched if row.status == "settled")
    pending_count = sum(1 for row in matched if row.status == "pending")
    failed_count = sum(1 for row in matched if row.status == "failed")

    return SettlementSummaryEnvelope(
        generated_at=_REFERENCE_NOW,
        filters=filters,
        total_count=len(matched),
        settled_count=settled_count,
        pending_count=pending_count,
        failed_count=failed_count,
        total_amount=total_amount,
        rows=matched,
    )


# ---------------------------------------------------------------------------
# Dependency: resolve and validate query filters
# ---------------------------------------------------------------------------


def get_settlement_report_filter(
    account_id: Optional[str] = Query(default=None, description="Filter by account id."),
    asset_code: Optional[str] = Query(default=None, description="Filter by asset code."),
    status: Optional[str] = Query(default=None, description="Filter by settlement status."),
    settlement_id: Optional[str] = Query(default=None, description="Filter by settlement id."),
    start_date: Optional[date] = Query(default=None, description="Inclusive start date."),
    end_date: Optional[date] = Query(default=None, description="Inclusive end date."),
) -> SettlementReportFilter:
    """Construct a validated :class:`SettlementReportFilter` from query params.

    Validation errors raised by the schema are surfaced as HTTP 422 responses
    with the stable workspace error shape via the router's exception handler.
    """

    return SettlementReportFilter(
        account_id=account_id,
        asset_code=asset_code,
        status=status,
        settlement_id=settlement_id,
        start_date=start_date,
        end_date=end_date,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=None)
def list_settlement_reports(
    filters: SettlementReportFilter = Depends(get_settlement_report_filter),
) -> JSONResponse:
    """Return a deterministic settlement summary for the provided filters."""

    summary = _build_summary(filters)
    return success_response(summary)


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------


def _handle_workspace_error(request: Request, exc: Exception) -> JSONResponse:
    """Map workspace validation errors to the stable error response shape."""

    http_exc = to_http_exception(exc)
    return JSONResponse(
        status_code=http_exc.status_code,
        content=http_exc.detail,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register workspace exception handlers on ``app``.

    Kept as an explicit helper so tests can attach handlers to an isolated
    ``FastAPI`` instance without touching the core application.
    """

    app.add_exception_handler(Exception, _handle_workspace_error)


# Register handlers on the router so that including the router in an app also
# wires up the workspace error mapping. FastAPI routers do not expose a direct
# exception-handler API, so we attach handlers when the router is included.
def _attach_handlers(app: FastAPI) -> None:
    register_exception_handlers(app)


# Provide a convenience factory for tests and isolated usage.
def create_app() -> FastAPI:
    """Create a minimal FastAPI app exposing only the settlement reports router."""

    app = FastAPI(title="SLA Operations - Settlement Reports")
    app.include_router(router)
    register_exception_handlers(app)
    return app


__all__ = [
    "router",
    "create_app",
    "register_exception_handlers",
    "get_settlement_report_filter",
    "list_settlement_reports",
]