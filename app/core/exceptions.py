from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError


class ApexConflictError(Exception):
    def __init__(self, detail: str, fields: Optional[Dict[str, str]] = None):
        self.detail = detail
        self.fields = fields or {}


class ApexValidationError(Exception):
    def __init__(self, detail: str, errors: Optional[List[Dict[str, Any]]] = None):
        self.detail = detail
        self.errors = errors or []


def _extract_integrity_fields(exc: IntegrityError) -> Dict[str, str]:
    msg = str(exc.orig)
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
    body: Dict[str, Any] = {
        "type": f"https://developer.apexchainx.io/errors/{status}",
        "title": title,
        "status": status,
        "detail": detail,
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
    return JSONResponse(status_code=409, content=body)


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
    return JSONResponse(status_code=422, content=body)
