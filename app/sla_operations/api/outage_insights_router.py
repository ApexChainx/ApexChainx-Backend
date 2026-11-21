from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.sla_operations.api.error_mapping import to_http_exception
from app.sla_operations.api.responses import success_response
from app.sla_operations.schemas.outage_insights import (
    OutageInsightQuery,
    OutageInsightsReport,
)

router = APIRouter(prefix="/sla-operations", tags=["sla-operations"])


class WorkspaceAPIError(ValueError):
    """Workspace-local error raised for invalid SLA operations requests.

    Subclasses ``ValueError`` so that callers may catch either type. The
    ``to_http_exception`` helper maps this to a stable HTTP error response.
    """

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _build_report(query: OutageInsightQuery) -> OutageInsightsReport:
    """Build a deterministic in-memory outage insights report.

    The report is derived purely from the validated query parameters so that
    the endpoint is deterministic and isolated from core application state.
    """

    if query.start_date and query.end_date and query.start_date > query.end_date:
        raise WorkspaceAPIError(
            "start_date must be on or before end_date",
            status_code=422,
        )

    # Deterministic, query-derived sample data. No external state is touched.
    total_outages = max(0, query.limit)
    resolved_outages = total_outages // 2
    open_outages = total_outages - resolved_outages

    insights: List[Dict[str, Any]] = [
        {
            "outage_id": f"outage-{index:04d}",
            "severity": query.severity or "unknown",
            "status": "resolved" if index < resolved_outages else "open",
        }
        for index in range(total_outages)
    ]

    return OutageInsightsReport(
        total_outages=total_outages,
        resolved_outages=resolved_outages,
        open_outages=open_outages,
        severity=query.severity,
        start_date=query.start_date,
        end_date=query.end_date,
        insights=insights,
    )


@router.get("/outage-insights")
def get_outage_insights(
    query: OutageInsightQuery = Depends(),
) -> Dict[str, Any]:
    """Return a deterministic outage insights report for the given query."""

    report = _build_report(query)
    return success_response(report.model_dump())


async def _workspace_api_error_handler(
    request: Request, exc: WorkspaceAPIError
) -> JSONResponse:
    """Map workspace errors to the stable ``WorkspaceErrorResponse`` shape."""

    http_exc = to_http_exception(exc)
    return JSONResponse(
        status_code=http_exc.status_code,
        content=http_exc.detail,
    )


async def _value_error_handler(
    request: Request, exc: ValueError
) -> JSONResponse:
    """Map plain ``ValueError`` instances to a 422 error response."""

    http_exc = to_http_exception(exc)
    return JSONResponse(
        status_code=http_exc.status_code,
        content=http_exc.detail,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register workspace-local exception handlers on the given app."""

    app.add_exception_handler(WorkspaceAPIError, _workspace_api_error_handler)
    app.add_exception_handler(ValueError, _value_error_handler)
</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>