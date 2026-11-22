"""Utility helpers for the SLA operations workspace.

This package exposes lightweight, dependency-free helpers that can be reused
by workspace services and schemas. The public surface is re-exported here so
callers can simply do::

    from app.sla_operations.utils import Ok, Err, Result

without needing to know the underlying module layout.
"""

from app.sla_operations.utils.result_types import (
    # Core result container types
    Result,
    Ok,
    Err,
    # Error types
    WorkspaceError,
    # Error-code constants
    ERROR_CODE_VALIDATION,
    ERROR_CODE_NOT_FOUND,
    ERROR_CODE_CONFLICT,
    ERROR_CODE_INTERNAL,
    ERROR_CODE_UNAUTHORIZED,
    ERROR_CODE_FORBIDDEN,
    ERROR_CODE_BAD_REQUEST,
    ERROR_CODE_UNPROCESSABLE,
    ERROR_CODE_UNKNOWN,
    ALL_ERROR_CODES,
    # Constructor / validation helpers
    ok,
    err,
    is_ok,
    is_err,
    unwrap,
    unwrap_or,
    unwrap_err,
    map_result,
    map_error,
    make_error,
    validate_error_code,
    normalize_error_code,
    combine_results,
)

__all__ = [
    # Core result container types
    "Result",
    "Ok",
    "Err",
    # Error types
    "WorkspaceError",
    # Error-code constants
    "ERROR_CODE_VALIDATION",
    "ERROR_CODE_NOT_FOUND",
    "ERROR_CODE_CONFLICT",
    "ERROR_CODE_INTERNAL",
    "ERROR_CODE_UNAUTHORIZED",
    "ERROR_CODE_FORBIDDEN",
    "ERROR_CODE_BAD_REQUEST",
    "ERROR_CODE_UNPROCESSABLE",
    "ERROR_CODE_UNKNOWN",
    "ALL_ERROR_CODES",
    # Constructor / validation helpers
    "ok",
    "err",
    "is_ok",
    "is_err",
    "unwrap",
    "unwrap_or",
    "unwrap_err",
    "map_result",
    "map_error",
    "make_error",
    "validate_error_code",
    "normalize_error_code",
    "combine_results",
]