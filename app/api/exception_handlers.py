"""RFC 7807 (Problem Details) exception handlers for all 4xx/5xx responses.

Every error response is normalised to ``application/problem+json`` so that
SDKs and partners can rely on stable machine-readable fields instead of
branching on cosmetic strings.

The shape is intentionally minimal — the spec allows extension members
(``correlation_id``, ``errors``) while keeping ``type``, ``title``,
``status``, and ``detail`` stable.

Issue #563: ``_problem_response`` now accepts an optional ``request`` argument.
When supplied it reads ``request.state.correlation_id`` — the ID captured by
``CorrelationMiddleware`` from the incoming ``X-Correlation-ID`` header (or
generated for that request) — so 4xx error responses always echo the *same* ID
that appears in the access log for that request rather than generating a fresh
one.  4xx log lines also include the correlation ID so they can be correlated
with the access log entry.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.correlation_ctx import get_or_generate_correlation_id

logger = logging.getLogger(__name__)


class ProblemDetail(BaseModel):
    """RFC 7807 problem detail envelope.

    Extension members:
      - ``correlation_id`` – ties the error back to the request log.
      - ``errors`` – optional list of field-level errors (validation).
    """

    type: str = Field(
        default="about:blank",
        description="A URI reference identifying the problem type.",
    )
    title: str = Field(description="A short, human-readable summary.")
    status: int = Field(description="The HTTP status code.")
    detail: str = Field(default="", description="A human-readable explanation.")
    correlation_id: str | None = Field(default=None)
    errors: list[dict[str, Any]] | None = Field(default=None)


def _resolve_correlation_id(request: Request | None) -> str:
    """Return the correlation ID for this request.

    Priority:
    1. ``request.state.correlation_id`` – set by ``CorrelationMiddleware`` from
       the incoming ``X-Correlation-ID`` header (or auto-generated once per
       request).  This is the canonical source and must be echoed so the body
       and the access-log entry carry the same ID (issue #563).
    2. The context-variable set by the same middleware (same value, different
       access path – used when no ``Request`` is available).
    3. Generate a fresh UUID as a last resort (e.g. startup-time errors that
       fire before any request is in flight).
    """
    if request is not None:
        cid = getattr(request.state, "correlation_id", None)
        if cid:
            return cid
    return get_or_generate_correlation_id()


def _problem_response(
    status: int,
    title: str,
    detail: str = "",
    errors: list[dict[str, Any]] | None = None,
    error_code: str | None = None,
    request: Request | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build an RFC 7807 JSON response, echoing the request's correlation ID.

    ``request`` should always be supplied from exception handlers that receive
    it (all FastAPI/Starlette exception handlers do).  It is kept optional only
    for call-sites that have not yet been updated or that run outside a request
    context.

    ``headers`` carries any extra headers attached to the originating
    ``HTTPException`` (for example ``Retry-After`` on a 429 rate-limit
    response); they are merged with — but may not override — the correlation
    ID header from issue #563.
    """
    correlation_id = _resolve_correlation_id(request)

    # #563: 4xx responses are logged at WARNING so they can be correlated with
    # the access log by correlation ID. (The CorrelationIdFilter stamps the
    # record's correlation_id from the context var, which the middleware keeps
    # in sync with request.state.)
    if 400 <= status < 500:
        logger.warning(
            "Problem response: %s %s",
            status,
            title,
            extra={
                "correlation_id": correlation_id,
                "path": request.url.path if request is not None else None,
                "status_code": status,
            },
        )

    problem = ProblemDetail(
        type="about:blank",
        title=title,
        status=status,
        detail=detail,
        correlation_id=correlation_id,
        errors=errors,
    )
    body = problem.model_dump(exclude_none=True)
    # #569: registered machine-readable codes ride along as an RFC 7807
    # extension member (see docs/ERROR_CODES.md) when the raiser provides one.
    if error_code:
        body["error_code"] = error_code

    merged_headers: dict[str, str] = dict(headers) if headers else {}
    # The correlation ID header wins: exception headers are merged with, but
    # may not override, it (issue #563).
    merged_headers["X-Correlation-ID"] = correlation_id
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=merged_headers,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle all HTTPException instances as RFC 7807 problem responses."""
    if isinstance(exc.detail, str):
        return _problem_response(
            status=exc.status_code,
            title=_default_title(exc.status_code),
            detail=exc.detail,
            # #569: endpoints may attach a registered code (docs/ERROR_CODES.md)
            error_code=getattr(exc, "error_code", None),
            request=request,
            headers=exc.headers,
        )

    errors: list[dict[str, Any]]

    if isinstance(exc.detail, dict):
        errors = [exc.detail]
    elif isinstance(exc.detail, list):
        errors = cast(list[dict[str, Any]], exc.detail)
    else:
        errors = [{"detail": str(exc.detail)}]

    return _problem_response(
        status=exc.status_code,
        title=_default_title(exc.status_code),
        detail="Request failed.",
        errors=errors,
        request=request,
        headers=exc.headers,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic validation errors with field-level ``errors[].pointer``."""
    errors = []
    for e in exc.errors():
        pointer = "/" + "/".join(str(loc) for loc in e.get("loc", []))
        errors.append(
            {
                "pointer": pointer,
                "type": e.get("type", ""),
                "message": e.get("msg", str(e)),
            }
        )
    return _problem_response(
        status=422,
        title="Unprocessable Entity",
        detail="Request validation failed.",
        errors=errors,
        request=request,
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort catch-all for unhandled exceptions (500)."""
    correlation_id = _resolve_correlation_id(request)
    # Log the full exception server-side (with traceback + correlation ID) so
    # 500s are never invisible, while keeping the client response sanitized.
    # Control-flow exceptions (e.g. cancellation) are not errors and are skipped.
    if not isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        logger.exception(
            "Unhandled exception",
            extra={"correlation_id": correlation_id, "path": request.url.path},
        )
    return _problem_response(
        status=500,
        title="Internal Server Error",
        detail="An unexpected error occurred.",
        request=request,
    )


def _default_title(status_code: int) -> str:
    """Return a reasonable title for standard HTTP status codes."""
    titles = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        410: "Gone",
        413: "Payload Too Large",
        415: "Unsupported Media Type",
        422: "Unprocessable Entity",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }
    return titles.get(status_code, "Unknown Error")
