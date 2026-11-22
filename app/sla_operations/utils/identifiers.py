"""Utilities for validating and formatting operational identifiers.

This module provides small, dependency-light helpers for working with
operational identifiers used across the SLA operations workspace, such as
outage IDs, payment references, and webhook IDs.

The helpers are pure functions with predictable behavior and are independent
of any core application state (no DB, no settings, no logging side effects).

Accepted identifier format (default policy)
--------------------------------------------
By default an identifier is considered valid when, after trimming leading and
trailing whitespace, it:

* is a ``str`` instance,
* is not empty (``min_length`` defaults to ``1``),
* is no longer than ``max_length`` characters (defaults to ``255``),
* matches the optional ``pattern`` when one is supplied.

Callers may override ``min_length``, ``max_length`` and ``pattern`` to enforce
domain-specific rules. When a ``pattern`` is provided it is applied to the
*trimmed* value using :func:`re.fullmatch`, so the pattern must match the whole
identifier.

Formatting policy
-----------------
:func:`normalize_identifier` and :func:`format_identifier` trim surrounding
whitespace and then optionally:

* apply a ``prefix`` (e.g. ``"OUT-"``) when the value does not already start
  with it, and
* upper-case the value when ``uppercase=True``.

The prefix is applied *before* upper-casing, so a lower-case prefix is
normalized consistently with the rest of the identifier.
"""

from __future__ import annotations

import re
from typing import Optional, Pattern, Union

__all__ = [
    "IdentifierValidationError",
    "is_valid_identifier",
    "validate_identifier",
    "normalize_identifier",
    "format_identifier",
]

#: Default maximum identifier length.
DEFAULT_MAX_LENGTH = 255

#: Default minimum identifier length.
DEFAULT_MIN_LENGTH = 1

#: Type alias for a compiled or string regular expression pattern.
PatternLike = Union[str, "Pattern[str]"]


class IdentifierValidationError(ValueError):
    """Raised when an operational identifier fails validation.

    Subclasses :class:`ValueError` so callers may catch either the specific
    error or the broader built-in exception.
    """


def _coerce_pattern(pattern: Optional[PatternLike]) -> Optional["Pattern[str]"]:
    """Return a compiled pattern for ``pattern`` or ``None``.

    Accepts either an already-compiled pattern or a string. Raises
    :class:`IdentifierValidationError` if a string pattern cannot be compiled.
    """
    if pattern is None:
        return None
    if isinstance(pattern, re.Pattern):
        return pattern
    try:
        return re.compile(pattern)
    except re.error as exc:  # pragma: no cover - defensive
        raise IdentifierValidationError(
            f"Invalid identifier pattern {pattern!r}: {exc}"
        ) from exc


def _check_common(
    value: object,
    *,
    pattern: Optional[PatternLike],
    min_length: int,
    max_length: int,
) -> str:
    """Validate ``value`` against the common rules and return the trimmed value.

    Raises :class:`IdentifierValidationError` on any violation. This helper is
    shared by :func:`is_valid_identifier` and :func:`validate_identifier`.
    """
    if not isinstance(value, str):
        raise IdentifierValidationError(
            f"Identifier must be a string, got {type(value).__name__}"
        )

    if min_length < 0:
        raise IdentifierValidationError("min_length must be non-negative")
    if max_length < min_length:
        raise IdentifierValidationError(
            "max_length must be greater than or equal to min_length"
        )

    trimmed = value.strip()

    if not trimmed:
        raise IdentifierValidationError("Identifier must not be empty or whitespace-only")

    if len(trimmed) < min_length:
        raise IdentifierValidationError(
            f"Identifier must be at least {min_length} characters long"
        )

    if len(trimmed) > max_length:
        raise IdentifierValidationError(
            f"Identifier must be at most {max_length} characters long"
        )

    compiled = _coerce_pattern(pattern)
    if compiled is not None and compiled.fullmatch(trimmed) is None:
        raise IdentifierValidationError(
            f"Identifier {trimmed!r} does not match required pattern "
            f"{compiled.pattern!r}"
        )

    return trimmed


def is_valid_identifier(
    value: object,
    *,
    pattern: Optional[PatternLike] = None,
    min_length: int = DEFAULT_MIN_LENGTH,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> bool:
    """Return ``True`` if ``value`` is a valid operational identifier.

    Parameters
    ----------
    value:
        Candidate identifier. Non-string values are always invalid.
    pattern:
        Optional regular expression (string or compiled) that the trimmed
        identifier must fully match.
    min_length:
        Minimum length of the trimmed identifier. Defaults to ``1``.
    max_length:
        Maximum length of the trimmed identifier. Defaults to ``255``.

    Returns
    -------
    bool
        ``True`` when the value passes all checks, ``False`` otherwise.

    Edge behavior
    -------------
    * Non-string inputs (``None``, ``int``, etc.) return ``False``.
    * Empty or whitespace-only strings return ``False``.
    * Values longer than ``max_length`` (after trimming) return ``False``.
    * Invalid ``min_length``/``max_length`` combinations return ``False``.
    * An uncompilable ``pattern`` string raises
      :class:`IdentifierValidationError` rather than returning ``False``,
      because it indicates a programming error rather than bad input.
    """
    try:
        _check_common(
            value,
            pattern=pattern,
            min_length=min_length,
            max_length=max_length,
        )
    except IdentifierValidationError:
        return False
    return True


def validate_identifier(
    value: object,
    *,
    pattern: Optional[PatternLike] = None,
    min_length: int = DEFAULT_MIN_LENGTH,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> str:
    """Validate ``value`` and return the trimmed identifier.

    Parameters
    ----------
    value:
        Candidate identifier.
    pattern:
        Optional regular expression (string or compiled) that the trimmed
        identifier must fully match.
    min_length:
        Minimum length of the trimmed identifier. Defaults to ``1``.
    max_length:
        Maximum length of the trimmed identifier. Defaults to ``255``.

    Returns
    -------
    str
        The trimmed identifier.

    Raises
    ------
    IdentifierValidationError
        If ``value`` is not a string, is empty/whitespace-only, is outside the
        length bounds, does not match ``pattern``, or if the length bounds or
        pattern are themselves invalid.

    Edge behavior
    -------------
    The returned value has leading and trailing whitespace removed; internal
    whitespace is preserved. Case is not modified.
    """
    return _check_common(
        value,
        pattern=pattern,
        min_length=min_length,
        max_length=max_length,
    )


def normalize_identifier(
    value: object,
    *,
    prefix: Optional[str] = None,
    uppercase: bool = False,
) -> str:
    """Normalize an identifier by trimming whitespace and applying formatting.

    Parameters
    ----------
    value:
        Identifier to normalize. Must be a string.
    prefix:
        Optional prefix to ensure at the start of the identifier. If the
        trimmed value does not already start with ``prefix``, it is prepended.
        The prefix is applied before upper-casing.
    uppercase:
        When ``True``, the resulting identifier is upper-cased.

    Returns
    -------
    str
        The normalized identifier.

    Raises
    ------
    IdentifierValidationError
        If ``value`` is not a string, or if ``prefix`` is provided but is not
        a string.

    Edge behavior
    -------------
    * Leading/trailing whitespace is always removed.
    * An empty or whitespace-only ``value`` normalizes to ``""`` (or to the
      ``prefix`` when one is supplied).
    * Prefix matching is case-sensitive and performed on the trimmed value
      before upper-casing.
    """
    if not isinstance(value, str):
        raise IdentifierValidationError(
            f"Identifier must be a string, got {type(value).__name__}"
        )

    if prefix is not None and not isinstance(prefix, str):
        raise IdentifierValidationError(
            f"prefix must be a string, got {type(prefix).__name__}"
        )

    result = value.strip()

    if prefix:
        if not result.startswith(prefix):
            result = f"{prefix}{result}"

    if uppercase:
        result = result.upper()

    return result


def format_identifier(
    value: object,
    *,
    prefix: Optional[str] = None,
    uppercase: bool = False,
) -> str:
    """Format an identifier for display or storage.

    This is an alias for :func:`normalize_identifier` and follows the same
    trimming, prefix, and case policy. It exists to provide a semantically
    clearer name for call sites that format identifiers for output.

    Parameters
    ----------
    value:
        Identifier to format. Must be a string.
    prefix:
        Optional prefix to ensure at the start of the identifier.
    uppercase:
        When ``True``, the resulting identifier is upper-cased.

    Returns
    -------
    str
        The formatted identifier.

    Raises
    ------
    IdentifierValidationError
        If ``value`` is not a string, or if ``prefix`` is provided but is not
        a string.
    """
    return normalize_identifier(value, prefix=prefix, uppercase=uppercase)