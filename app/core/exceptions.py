from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError


from app.utils.correlation_ctx import get_or_generate_correlation_id


class ApexException(Exception):
114-117-webhook-concurrency-canonical-json
    status_code: int = 500
    error_code: str = "error"
    extra: dict[str, Any] | None = None

    def __init__(
        self,
        detail: str,
        *,
        status_code: int = 500,
        error_code: str = "error",
        extra: dict[str, Any] | None = None,
    ):
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code
        self.extra = extra
        super().__init__(detail)

    """Base exception for all ApexChainx domain errors."""

    def __init__(self, detail: str, status_code: int = 500):
        self.detail = detail
        self.status_code = status_code


class ApexNotFoundError(ApexException):
    """Raised when a requested resource does not exist."""

    def __init__(self, detail: str = "Resource not found"):
        super().__init__(detail=detail, status_code=404)


class ApexTransientError(ApexException):
    """Raised for transient infrastructure errors that may succeed on retry."""

    def __init__(self, detail: str = "A transient error occurred. Please retry."):
        super().__init__(detail=detail, status_code=503)
main


class ApexConflictError(ApexException):
    def __init__(self, detail: str, fields: Optional[Dict[str, str]] = None):
114-117-webhook-concurrency-canonical-json
        super().__init__(
            detail,
            status_code=409,
            error_code="conflict",
            extra={"fields": fields or {}},
        )

        super().__init__(detail=detail, status_code=409)
        self.fields = fields or {}
main


class ApexValidationError(ApexException):
    def __init__(self, detail: str, errors: Optional[List[Dict[str, Any]]] = None):
issue/114-117-webhook-concurrency-canonical-json
        super().__init__(
            detail,
            status_code=422,
            error_code="validation_error",
            extra={"errors": errors or []},
        )


class ApexNotFoundError(ApexException):
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(detail, status_code=404, error_code="not_found")


class ApexTransientError(ApexException):
    def __init__(self, detail: str = "A transient error occurred"):
        super().__init__(
            detail,
            status_code=500,
            error_code="transient_error",
            extra={"retryable": True},
        )

        super().__init__(detail=detail, status_code=422)
        self.errors = errors or []
main


def _extract_integrity_fields(exc: IntegrityError) -> Dict[str, str]:
    # Use the first arg of exc.orig (the raw psycopg2/driver message) when available;
    # fall back to str(exc.orig) for other drivers.
    orig = exc.orig
    if orig is not None and hasattr(orig, "args") and orig.args:
        msg = str(orig.args[0])
    else:
        msg = str(orig)
    fields: Dict[str, str] = {}
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
    instance: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    correlation_id = extra.pop("correlation_id", None) or get_or_generate_correlation_id()
    body: Dict[str, Any] = {
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
        errors.append({
            "field": loc,
            "message": err.get("msg", ""),
            "code": err.get("type", ""),
        })
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
