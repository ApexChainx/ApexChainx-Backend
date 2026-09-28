"""Shared query-param dependencies for list endpoints (issue #564).

Every list endpoint used to declare its own ``page`` / ``page_size`` /
``limit`` query parameters with ad-hoc caps (webhook deliveries le=200,
webhooks le=100, payments le=100 — and the SLA dispute list and audit log
had no cap at all, so an authenticated client could pull a full-table dump).

The dependencies here give every list endpoint the same defaults and the same
hard cap from ``app.schemas.pagination``. Out-of-range values are rejected by
FastAPI with 422 before the handler runs.

Usage::

    @router.get("")
    def list_things(
        page: PageParams,
        page_size: PageSizeParams,
        ...
    ):

``PageParams`` / ``LimitParams`` are named so generated OpenAPI documents show
the shared contract per endpoint rather than a bare ``Query(...)`` default.
"""

from __future__ import annotations

from fastapi import Query

from app.schemas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

PageParams = Query(
    default=1,
    ge=1,
    le=10_000,
    description="Page number (1-indexed).",
)
PageSizeParams = Query(
    default=DEFAULT_PAGE_SIZE,
    ge=1,
    le=MAX_PAGE_SIZE,
    description=f"Items per page (1-{MAX_PAGE_SIZE}).",
)
LimitParams = Query(
    default=DEFAULT_PAGE_SIZE,
    ge=1,
    le=MAX_PAGE_SIZE,
    description=f"Max items per page (1-{MAX_PAGE_SIZE}).",
)
OffsetParams = Query(
    default=0,
    ge=0,
    description="Number of records to skip.",
)
