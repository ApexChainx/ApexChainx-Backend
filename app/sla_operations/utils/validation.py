"""Reusable validation helpers for the SLA operations workspace.

This module provides small, dependency-light (stdlib only) validation
utilities intended for reuse by workspace services, schemas, and analytics
code. All helpers are pure functions with predictable behavior and raise
:class:`ValidationError` (a subclass of :class:`ValueError`) on invalid
input, so callers may catch either the specific or the generic exception.

The helpers are independent of core application state and perform no I/O.
"""

from __future__ import annotations

import re
from numbers import Real
from typing import Any, Iterable, Mapping, Optional, Pattern

__all__ = [
    "ValidationError",
    "require_non_empty_str",
    "validate_bounded_number",
    "validate_identifier",
    "validate_record",
]

# Safe default pattern for identifiers: starts with a letter, followed by
# letters, digits, underscores, or hyphens. Length is bounded to avoid abuse.
_DEFAULT_IDENTIFIER_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,127}$"


class ValidationError(ValueError):
    """Explicit workspace validation error.

    Subclasses :class:`ValueError` so existing ``except ValueError`` handlers
    continue to work while allowing callers to catch workspace-specific
    validation failures precisely.

    Parameters
    ----------
    message:
        Human-readable description of the validation failure.
    field_name:
        Optional name of the offending field, stored for programmatic use.
    """

    def __init__(self, message: str, field_name: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_name = field_name


def require_non_empty_str(value: Any, field_name: str) -> str:
    """Return a stripped, non-empty string.

    Parameters
    ----------
    value:
        The value to validate. Must be a :class:`str`.
    field_name:
        Name of the field, used in error messages.

    Returns
    -------
    str
        The input string with leading/trailing whitespace removed.

    Raises
    ------
    ValidationError
        If ``value`` is not a string, or if it is empty or whitespace-only
        after stripping.
    """
    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string, got {type(value).__name__}",
            field_name=field_name,
        )
    stripped = value.strip()
    if not stripped:
        raise ValidationError(
            f"{field_name} must be a non-empty string",
            field_name=field_name,
        )
    return stripped


def validate_bounded_number(
    value: Any,
    field_name: str,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """Validate a numeric value and return it as a float.

    Parameters
    ----------
    value:
        The value to validate. Must be a real number (``int`` or ``float``).
        Booleans are rejected even though they subclass ``int``.
    field_name:
        Name of the field, used in error messages.
    minimum:
        Optional inclusive lower bound.
    maximum:
        Optional inclusive upper bound.

    Returns
    -------
    float
        The validated value converted to ``float``.

    Raises
    ------
    ValidationError
        If ``value`` is not a real number, is a boolean, is NaN/infinite,
        or falls outside the inclusive ``[minimum, maximum]`` range.
    """
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValidationError(
            f"{field_name} must be a number, got {type(value).__name__}",
            field_name=field_name,
        )

    number = float(value)

    # Reject NaN and infinities which would break range comparisons.
    if number != number:  # NaN check without importing math
        raise ValidationError(
            f"{field_name} must be a finite number, got NaN",
            field_name=field_name,
        )
    if number in (float("inf"), float("-inf")):
        raise ValidationError(
            f"{field_name} must be a finite number, got infinity",
            field_name=field_name,
        )

    if minimum is not None and number < minimum:
        raise ValidationError(
            f"{field_name} must be >= {minimum}, got {number}",
            field_name=field_name,
        )
    if maximum is not None and number > maximum:
        raise ValidationError(
            f"{field_name} must be <= {maximum}, got {number}",
            field_name=field_name,
        )
    return number


def validate_identifier(
    value: Any,
    field_name: str,
    pattern: Optional[str] = None,
) -> str:
    """Validate an identifier string against a regular expression.

    Parameters
    ----------
    value:
        The value to validate. Must be a non-empty string.
    field_name:
        Name of the field, used in error messages.
    pattern:
        Optional regular expression (as a string) the identifier must fully
        match. When omitted, a safe default pattern is used: it must start
        with a letter and contain only letters, digits, underscores, or
        hyphens, with a maximum length of 128 characters.

    Returns
    -------
    str
        The stripped identifier string.

    Raises
    ------
    ValidationError
        If ``value`` is not a non-empty string, if ``pattern`` is not a valid
        regular expression, or if the identifier does not match the pattern.
    """
    identifier = require_non_empty_str(value, field_name)

    regex_source = pattern if pattern is not None else _DEFAULT_IDENTIFIER_PATTERN
    try:
        compiled: Pattern[str] = re.compile(regex_source)
    except re.error as exc:
        raise ValidationError(
            f"invalid pattern for {field_name}: {exc}",
            field_name=field_name,
        ) from exc

    if compiled.fullmatch(identifier) is None:
        raise ValidationError(
            f"{field_name} does not match required pattern",
            field_name=field_name,
        )
    return identifier


def validate_record(
    record: Any,
    required_fields: Iterable[str],
    optional_fields: Optional[Iterable[str]] = None,
) -> dict:
    """Validate a mapping record and return a normalized copy.

    Parameters
    ----------
    record:
        The record to validate. Must be a mapping (e.g. ``dict``).
    required_fields:
        Field names that must be present with non-empty values.
    optional_fields:
        Field names that may be present. Unknown fields are ignored in the
        returned copy.

    Returns
    -------
    dict
        A new dictionary containing only the required and optional fields
        that were present. String values are stripped; ``None`` values for
        optional fields are preserved as ``None``.

    Raises
    ------
    ValidationError
        If ``record`` is not a mapping, or if any required field is missing
        or empty. The error message lists all offending fields.
    """
    if not isinstance(record, Mapping):
        raise ValidationError(
            f"record must be a mapping, got {type(record).__name__}",
        )

    required = list(required_fields)
    optional = list(optional_fields) if optional_fields is not None else []

    problems: list[str] = []
    normalized: dict = {}

    for field in required:
        if field not in record:
            problems.append(f"missing required field '{field}'")
            continue
        raw = record[field]
        if raw is None:
            problems.append(f"required field '{field}' is None")
            continue
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                problems.append(f"required field '{field}' is empty")
                continue
            normalized[field] = stripped
        else:
            normalized[field] = raw

    for field in optional:
        if field not in record:
            continue
        raw = record[field]
        if isinstance(raw, str):
            normalized[field] = raw.strip()
        else:
            normalized[field] = raw

    if problems:
        raise ValidationError(
            "invalid record: " + "; ".join(problems),
        )
    return normalized