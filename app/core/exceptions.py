from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.utils.correlation_ctx import get_or_generate_correlation_id


class ApexException(Exception):
    """Base exception for all ApexChainx domain errors.

    Every raised exception in the codebase should subclass this or one
    of its children so that middleware and error handlers can act on
    typed conditions rather than bare ``except Exception``.
    """

    def __init__(
        self,
        detail: str = "An unexpected error occurred.",
        *,
        error_code: str = "internal_error",
        status_code: int = 500,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail
        self.error_code = error_code
        self.status_code = status_code
        self.extra = extra or {}
        super().__init__(detail)


class ApexNotFoundError(ApexException):
    """Resource not found (404)."""

    def __init__(self, detail: str = "Resource not found.", **kwargs: Any) -> None:
        super().__init__(detail=detail, error_code="not_found", status_code=404, **kwargs)


class ApexTransientError(ApexException):
    """Transient / retryable error (503 by default)."""

    def __init__(self, detail: str = "A transient error occurred.", **kwargs: Any) -> None:
        super().__init__(detail=detail, error_code="transient_error", status_code=503, **kwargs)


class ApexConflictError(ApexException):
    def __init__(self, detail: str, fields: dict[str, str] | None = None):
        super().__init__(detail=detail, error_code="conflict", status_code=409)
        self.fields = fields or {}


class ApexValidationError(ApexException):
    def __init__(self, detail: str, errors: list[dict[str, Any]] | None = None):
        super().__init__(detail=detail, error_code="validation_error", status_code=422)
        self.errors = errors or []


def _extract_integrity_fields(exc: IntegrityError) -> dict[str, str]:
    # Use the first arg of exc.orig (the raw psycopg2/driver message) when available;
    # fall back to str(exc.orig) for other drivers.
    orig = exc.orig
    if orig is not None and hasattr(orig, "args") and orig.args:
        msg = str(orig.args[0])
    else:
        msg = str(orig)
    fields: dict[str, str] = {}
    if "Key (" in msg:
        for part in msg.split("Key ")[1:]:
            if " already exists" in part:
                key_part = part.split("(")[1].split(")")[0].strip()
                val_part = part.split("=(")[1].split(")")[0].strip() if "=(" in part else "unknown"
                fields[key_part] = val_part
    return fields


def _build_rfc7807(
    title: str,
    status: int,
    detail: str,
    instance: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    correlation_id = extra.pop("correlation_id", None) or get_or_generate_correlation_id()
    body: dict[str, Any] = {
        "type": f"https://developer.apexchainx.io/errors/{status}",
        "title": title,
        "status": status,
        "detail": detail,
        "correlation_id": correlation_id,
    }
    if instance:
        body["instance"] = instance
    body.update(extra)
    return body


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    fields = _extract_integrity_fields(exc)
    detail = "A record with the same unique fields already exists."
    body = _build_rfc7807(
        title="Conflict",
        status=409,
        detail=detail,
        instance=str(request.url.path),
        fields=fields,
    )
    correlation_id = body.get("correlation_id") or get_or_generate_correlation_id()
    return JSONResponse(
        status_code=409,
        content=body,
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
    )


async def pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
    raw = exc.errors()
    errors = []
    for err in raw:
        loc = " -> ".join(str(p) for p in err.get("loc", []))
        errors.append(
            {
                "field": loc,
                "message": err.get("msg", ""),
                "code": err.get("type", ""),
            }
        )
    body = _build_rfc7807(
        title="Validation Error",
        status=422,
        detail="The request body contains invalid fields.",
        instance=str(request.url.path),
        errors=errors,
    )
    correlation_id = body.get("correlation_id") or get_or_generate_correlation_id()
    return JSONResponse(
        status_code=422,
        content=body,
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
    )
