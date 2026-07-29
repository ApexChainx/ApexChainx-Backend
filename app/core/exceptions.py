from __future__ import annotations

from typing import Any


class ApexException(Exception):
    status_code: int = 500
    error_code: str = "internal_error"
    detail: str = "An unexpected error occurred"

    def __init__(self, detail: str | None = None, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail or self.detail)
        self.detail = detail or self.detail
        self.extra = extra or {}


class ApexValidationError(ApexException):
    status_code = 400
    error_code = "validation_error"
    detail = "Request validation failed"


class ApexNotFoundError(ApexException):
    status_code = 404
    error_code = "not_found"
    detail = "The requested resource was not found"


class ApexConflictError(ApexException):
    status_code = 409
    error_code = "conflict"
    detail = "The request conflicts with the current state"


class ApexTransientError(ApexException):
    status_code = 500
    error_code = "transient_error"
    detail = "A transient error occurred; the request may be retried"
