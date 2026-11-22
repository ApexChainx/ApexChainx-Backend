"""Text sanitization and normalization helpers for the SLA operations workspace.

This module provides small, dependency-light, pure functions for cleaning up
free-form text such as notes and descriptions before they are persisted or
rendered. The helpers are intentionally independent of any core application
state (database, settings, logging, etc.) so they can be reused freely by
workspace services and schemas.

All functions are deterministic and side-effect free.
"""

from __future__ import annotations

import unicodedata
from typing import Optional

__all__ = [
    "sanitize_text",
    "normalize_whitespace",
    "strip_control_characters",
    "truncate_text",
]


def _ensure_str(value: object) -> str:
    """Validate that ``value`` is a string and return it.

    Parameters:
        value: The value to validate.

    Returns:
        The original string.

    Raises:
        TypeError: If ``value`` is not a ``str`` instance.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"Expected a string, got {type(value).__name__!r}"
        )
    return value


def strip_control_characters(value: Optional[str]) -> str:
    """Remove non-printable control characters from ``value``.

    Control characters are identified using the Unicode category ``Cc``
    (which covers ASCII control characters such as ``\\x00``-``\\x1f`` and
    ``\\x7f`` as well as other C0/C1 controls). Newlines and tabs are also
    control characters and are therefore removed by this function; use
    :func:`sanitize_text` with ``allow_newlines=True`` if line breaks should
    be preserved.

    Parameters:
        value: The input text. ``None`` is treated as an empty string.

    Returns:
        A new string with all control characters removed. Returns ``''`` when
        ``value`` is ``None`` or contains only control characters.

    Raises:
        TypeError: If ``value`` is neither ``None`` nor a ``str``.
    """
    if value is None:
        return ""
    text = _ensure_str(value)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cc")


def normalize_whitespace(value: Optional[str]) -> str:
    """Collapse runs of whitespace into single spaces and trim the result.

    Any sequence of whitespace characters (spaces, tabs, newlines, and other
    Unicode whitespace) is replaced by a single ASCII space. Leading and
    trailing whitespace is removed.

    Parameters:
        value: The input text. ``None`` is treated as an empty string.

    Returns:
        The whitespace-normalized string. Returns ``''`` when ``value`` is
        ``None`` or consists only of whitespace.

    Raises:
        TypeError: If ``value`` is neither ``None`` nor a ``str``.
    """
    if value is None:
        return ""
    text = _ensure_str(value)
    return " ".join(text.split())


def truncate_text(value: Optional[str], max_length: int) -> str:
    """Truncate ``value`` to at most ``max_length`` characters.

    Truncation is performed on the character level and never splits a
    multi-byte character. No ellipsis or other marker is appended; the result
    is a plain prefix of the input.

    Parameters:
        value: The input text. ``None`` is treated as an empty string.
        max_length: The maximum number of characters to keep. Must be a
            non-negative integer.

    Returns:
        The (possibly truncated) string. Returns ``''`` when ``value`` is
        ``None`` or when ``max_length`` is ``0``.

    Raises:
        TypeError: If ``value`` is neither ``None`` nor a ``str``, or if
            ``max_length`` is not an ``int`` (``bool`` is rejected).
        ValueError: If ``max_length`` is negative.
    """
    if isinstance(max_length, bool) or not isinstance(max_length, int):
        raise TypeError(
            f"max_length must be an int, got {type(max_length).__name__!r}"
        )
    if max_length < 0:
        raise ValueError("max_length must be non-negative")
    if value is None:
        return ""
    text = _ensure_str(value)
    if len(text) <= max_length:
        return text
    return text[:max_length]


def sanitize_text(
    value: Optional[str],
    max_length: Optional[int] = None,
    allow_newlines: bool = False,
) -> str:
    """Sanitize free-form text for safe storage or display.

    The sanitization pipeline is:

    1. Control characters are removed. When ``allow_newlines`` is ``True``,
       line feed (``\\n``) and carriage return (``\\r``) characters are
       preserved (carriage returns are normalized to line feeds); all other
       control characters are still stripped.
    2. Whitespace is normalized. When ``allow_newlines`` is ``False``, all
       whitespace runs collapse to single spaces. When ``allow_newlines`` is
       ``True``, each line is individually stripped and internal whitespace
       runs collapse to single spaces, while line breaks are retained.
    3. The result is truncated to ``max_length`` characters if provided.

    Parameters:
        value: The input text. ``None`` is treated as an empty string.
        max_length: Optional maximum length of the returned string. Must be a
            non-negative integer when provided.
        allow_newlines: Whether to preserve line breaks. Defaults to ``False``.

    Returns:
        A sanitized string. Returns ``''`` when ``value`` is ``None`` or
        sanitizes to nothing.

    Raises:
        TypeError: If ``value`` is neither ``None`` nor a ``str``, if
            ``max_length`` is not an ``int`` (``bool`` is rejected), or if
            ``allow_newlines`` is not a ``bool``.
        ValueError: If ``max_length`` is negative.
    """
    if not isinstance(allow_newlines, bool):
        raise TypeError(
            f"allow_newlines must be a bool, got "
            f"{type(allow_newlines).__name__!r}"
        )
    if max_length is not None:
        if isinstance(max_length, bool) or not isinstance(max_length, int):
            raise TypeError(
                f"max_length must be an int or None, got "
                f"{type(max_length).__name__!r}"
            )
        if max_length < 0:
            raise ValueError("max_length must be non-negative")

    if value is None:
        return ""
    text = _ensure_str(value)

    if allow_newlines:
        # Normalize CRLF / CR to LF, then strip control chars except LF.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = "".join(
            ch
            for ch in text
            if ch == "\n" or unicodedata.category(ch) != "Cc"
        )
        lines = [" ".join(line.split()) for line in text.split("\n")]
        cleaned = "\n".join(lines).strip("\n")
    else:
        cleaned = normalize_whitespace(strip_control_characters(text))

    if max_length is not None:
        cleaned = truncate_text(cleaned, max_length)

    return cleaned