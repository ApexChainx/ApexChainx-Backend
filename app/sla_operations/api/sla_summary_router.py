"""SLA summary router for the SLA operations workspace.

This module exposes a single read-only endpoint that returns an aggregated
SLA operation summary for a given query window. It is intentionally free of
database and core-application imports so that it can be exercised in
isolation by the workspace test-suite.

Dependency override
-------------------
The data provider is injected through the FastAPI dependency named
``get_sla_summary_provider``. Tests (and other consumers) can override it via
``app.dependency_overrides[get_sla_summary_provider]`` to return a deterministic
``SLAOperationSummary`` or to raise a workspace error.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from app.sla_operations.api.error_mapping import map_workspace_error, to_http_exception
from app.sla_operations.api.responses import ResponseEnvelope, success_response
from app.sla_operations.schemas.sla_summary import SLASummaryQuery, SLAOperationSummary

__all__ = [
    "router",
    "get_sla_summary_provider",
    "SLAOperationSummaryProvider",
    "read_sla_summary",
]


# A provider is any callable that accepts a validated query and returns a
# summary, or raises ``WorkspaceAPIError`` / ``LookupError`` / ``ValueError``.
SLAOperationSummaryProvider = Callable[[SLASummaryQuery], SLAOperationSummary]


def get_sla_summary_provider() -> SLAOperationSummaryProvider:
    """Return the default SLA summary data provider.

    The default provider is a placeholder that raises ``LookupError`` so that
    the endpoint is meaningful (returns a mapped 404) until a real provider is
    wired in. Tests should override this dependency with a deterministic
    provider.
    """

    def _default_provider(query: SLASummaryQuery) -> SLAOperationSummary:
        raise LookupError(
            "No SLA summary provider configured for the requested window."
        )

    return _default_provider


router = APIRouter(prefix="/sla-summary", tags=["sla-operations"])


@router.get(
    "",
    response_model=ResponseEnvelope[SLAOperationSummary],
    summary="Get SLA operation summary",
    description=(
        "Return an aggregated SLA operation summary for the requested window. "
        "The underlying data provider is injected via the "
        "``get_sla_summary_provider`` dependency and may be overridden."
    ),
)
def read_sla_summary(
    query: SLASummaryQuery = Depends(),
    provider: SLAOperationSummaryProvider = Depends(get_sla_summary_provider),
) -> ResponseEnvelope[SLAOperationSummary]:
    """Return the SLA operation summary for ``query``.

    Errors raised by the provider are translated into HTTP responses using the
    workspace error-mapping helpers.
    """

    try:
        summary = provider(query)
    except (LookupError, ValueError) as exc:
        workspace_error = map_workspace_error(exc)
        raise to_http_exception(workspace_error) from exc
    except HTTPException:
        raise

    return success_response(summary)