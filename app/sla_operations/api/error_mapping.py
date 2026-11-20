"""Stable HTTP error envelopes for isolated SLA operations API modules."""

from fastapi import HTTPException
from pydantic import BaseModel, Field


class WorkspaceErrorResponse(BaseModel):
    """Public, stable error shape returned by workspace APIs."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class WorkspaceAPIError(Exception):
    """Expected workspace error with an explicit HTTP status and public message."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
    ) -> None:
        if not 400 <= status_code <= 599:
            raise ValueError("status_code must be between 400 and 599")
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def map_workspace_error(
    error: Exception,
) -> tuple[int, WorkspaceErrorResponse]:
    """Map known workspace failures to deterministic, non-sensitive HTTP errors."""
    if isinstance(error, WorkspaceAPIError):
        return error.status_code, WorkspaceErrorResponse(code=error.code, message=error.message)
    if isinstance(error, LookupError):
        return 404, WorkspaceErrorResponse(code="not_found", message="Requested resource was not found.")
    if isinstance(error, PermissionError):
        return 403, WorkspaceErrorResponse(code="forbidden", message="You are not allowed to perform this action.")
    if isinstance(error, ValueError):
        return 422, WorkspaceErrorResponse(code="invalid_request", message="The request contains invalid values.")
    return 500, WorkspaceErrorResponse(code="internal_error", message="An internal error occurred.")


def to_http_exception(error: Exception) -> HTTPException:
    """Convert a workspace exception to FastAPI's standard exception type."""
    status_code, response = map_workspace_error(error)
    return HTTPException(status_code=status_code, detail=response.model_dump())
