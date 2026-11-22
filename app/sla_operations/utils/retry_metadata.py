"""Utilities for interpreting and summarizing retry/backoff metadata.

This module provides pure, dependency-light helpers used by the SLA
operations workspace to reason about retry/backoff metadata attached to
operations insights. The helpers are intentionally free of any core
application state so they can be reused by services, schemas, and
analytics code.

Exported helpers:

* :func:`parse_retry_metadata`
* :func:`compute_backoff_delay`
* :func:`summarize_retry_metadata`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

__all__ = [
    "RetryMetadata",
    "SUPPORTED_BACKOFF_STRATEGIES",
    "parse_retry_metadata",
    "compute_backoff_delay",
    "summarize_retry_metadata",
]


#: Backoff strategies understood by :func:`compute_backoff_delay`.
SUPPORTED_BACKOFF_STRATEGIES: frozenset[str] = frozenset(
    {"fixed", "linear", "exponential"}
)

#: Default strategy used when none is supplied.
DEFAULT_BACKOFF_STRATEGY = "exponential"

#: Default base delay (in seconds) used when none is supplied.
DEFAULT_BASE_DELAY_SECONDS = 1.0

#: Default maximum number of retries used when none is supplied.
DEFAULT_MAX_RETRIES = 0


@dataclass(frozen=True)
class RetryMetadata:
    """Normalized representation of retry/backoff metadata.

    Attributes:
        attempts: Number of attempts already performed (>= 0).
        max_retries: Maximum number of retries allowed (>= 0).
        base_delay_seconds: Base delay between retries in seconds (>= 0).
        backoff_strategy: One of ``"fixed"``, ``"linear"``, or
            ``"exponential"``.
        last_error: Optional last error message, or ``None`` when absent.
    """

    attempts: int
    max_retries: int
    base_delay_seconds: float
    backoff_strategy: str
    last_error: str | None = None

    @property
    def remaining_retries(self) -> int:
        """Return the number of retries still available (never negative)."""
        return max(self.max_retries - self.attempts, 0)

    @property
    def exhausted(self) -> bool:
        """Return ``True`` when no retries remain."""
        return self.remaining_retries <= 0


def _coerce_int(value: Any, field_name: str) -> int:
    """Coerce ``value`` to a non-negative ``int``.

    Args:
        value: Raw value to coerce.
        field_name: Field name used in error messages.

    Returns:
        The coerced non-negative integer.

    Raises:
        ValueError: If ``value`` is not an integer-like value or is
            negative.
    """
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int``; reject it explicitly.
        raise ValueError(f"{field_name} must be an integer, got bool")
    if isinstance(value, int):
        result = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(
                f"{field_name} must be an integer, got {value!r}"
            )
        result = int(value)
    elif isinstance(value, str):
        try:
            result = int(value.strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{field_name} must be an integer, got {value!r}"
            ) from exc
    else:
        raise ValueError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if result < 0:
        raise ValueError(f"{field_name} must be non-negative, got {result}")
    return result


def _coerce_float(value: Any, field_name: str) -> float:
    """Coerce ``value`` to a non-negative ``float``.

    Args:
        value: Raw value to coerce.
        field_name: Field name used in error messages.

    Returns:
        The coerced non-negative float.

    Raises:
        ValueError: If ``value`` is not numeric or is negative.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number, got bool")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{field_name} must be a number, got {value!r}"
            ) from exc
    else:
        raise ValueError(
            f"{field_name} must be a number, got {type(value).__name__}"
        )
    if result != result:  # NaN check
        raise ValueError(f"{field_name} must be a finite number, got NaN")
    if result in (float("inf"), float("-inf")):
        raise ValueError(
            f"{field_name} must be a finite number, got {result!r}"
        )
    if result < 0:
        raise ValueError(f"{field_name} must be non-negative, got {result}")
    return result


def _coerce_strategy(value: Any) -> str:
    """Validate and normalize a backoff strategy name.

    Args:
        value: Raw strategy value.

    Returns:
        The normalized lowercase strategy name.

    Raises:
        ValueError: If the strategy is not a string or is unsupported.
    """
    if not isinstance(value, str):
        raise ValueError(
            "backoff_strategy must be a string, got "
            f"{type(value).__name__}"
        )
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_BACKOFF_STRATEGIES:
        raise ValueError(
            f"unsupported backoff_strategy {value!r}; expected one of "
            f"{sorted(SUPPORTED_BACKOFF_STRATEGIES)}"
        )
    return normalized


def parse_retry_metadata(
    metadata: Mapping[str, Any] | None,
) -> RetryMetadata:
    """Parse raw retry metadata into a :class:`RetryMetadata` instance.

    Missing or ``None`` fields fall back to safe defaults:

    * ``attempts`` -> ``0``
    * ``max_retries`` -> ``0``
    * ``base_delay_seconds`` -> ``1.0``
    * ``backoff_strategy`` -> ``"exponential"``
    * ``last_error`` -> ``None``

    Args:
        metadata: Mapping of raw retry metadata. ``None`` is treated as an
            empty mapping.

    Returns:
        A normalized :class:`RetryMetadata` instance.

    Raises:
        ValueError: If ``metadata`` is not a mapping, or if any provided
            field has an invalid type, is negative, or (for
            ``backoff_strategy``) is unsupported.
    """
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise ValueError(
            "metadata must be a mapping or None, got "
            f"{type(metadata).__name__}"
        )

    attempts_raw = metadata.get("attempts")
    attempts = (
        0 if attempts_raw is None else _coerce_int(attempts_raw, "attempts")
    )

    max_retries_raw = metadata.get("max_retries")
    max_retries = (
        DEFAULT_MAX_RETRIES
        if max_retries_raw is None
        else _coerce_int(max_retries_raw, "max_retries")
    )

    base_delay_raw = metadata.get("base_delay_seconds")
    base_delay_seconds = (
        DEFAULT_BASE_DELAY_SECONDS
        if base_delay_raw is None
        else _coerce_float(base_delay_raw, "base_delay_seconds")
    )

    strategy_raw = metadata.get("backoff_strategy")
    backoff_strategy = (
        DEFAULT_BACKOFF_STRATEGY
        if strategy_raw is None
        else _coerce_strategy(strategy_raw)
    )

    last_error_raw = metadata.get("last_error")
    if last_error_raw is None:
        last_error: str | None = None
    elif isinstance(last_error_raw, str):
        last_error = last_error_raw
    else:
        raise ValueError(
            "last_error must be a string or None, got "
            f"{type(last_error_raw).__name__}"
        )

    return RetryMetadata(
        attempts=attempts,
        max_retries=max_retries,
        base_delay_seconds=base_delay_seconds,
        backoff_strategy=backoff_strategy,
        last_error=last_error,
    )


def compute_backoff_delay(
    attempt: int,
    base_delay_seconds: float,
    strategy: str = DEFAULT_BACKOFF_STRATEGY,
    max_delay_seconds: float | None = None,
) -> float:
    """Compute the backoff delay for a given retry attempt.

    The delay is computed as follows (``attempt`` is 1-indexed):

    * ``"fixed"``: ``base_delay_seconds``
    * ``"linear"``: ``base_delay_seconds * attempt``
    * ``"exponential"``: ``base_delay_seconds * 2 ** (attempt - 1)``

    When ``max_delay_seconds`` is provided, the result is clamped to that
    upper bound.

    Args:
        attempt: 1-indexed retry attempt number (must be >= 1).
        base_delay_seconds: Base delay in seconds (must be >= 0).
        strategy: Backoff strategy; one of ``"fixed"``, ``"linear"``, or
            ``"exponential"``.
        max_delay_seconds: Optional upper bound for the computed delay
            (must be >= 0 when provided).

    Returns:
        The computed delay in seconds as a float.

    Raises:
        ValueError: If ``attempt`` is not a positive integer, if
            ``base_delay_seconds`` is negative or non-numeric, if
            ``strategy`` is unsupported, or if ``max_delay_seconds`` is
            negative or non-numeric.
    """
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise ValueError(
            f"attempt must be an integer, got {type(attempt).__name__}"
        )
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")

    base = _coerce_float(base_delay_seconds, "base_delay_seconds")
    normalized_strategy = _coerce_strategy(strategy)

    if normalized_strategy == "fixed":
        delay = base
    elif normalized_strategy == "linear":
        delay = base * attempt
    else:  # exponential
        delay = base * float(2 ** (attempt - 1))

    if max_delay_seconds is not None:
        max_delay = _coerce_float(max_delay_seconds, "max_delay_seconds")
        if delay > max_delay:
            delay = max_delay

    return float(delay)


def summarize_retry_metadata(
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Produce a compact summary of retry metadata.

    The summary is suitable for analytics and operational insights and
    contains the following keys:

    * ``attempts``: Number of attempts performed.
    * ``remaining_retries``: Retries still available (never negative).
    * ``next_delay_seconds``: Delay before the next retry, or ``None``
      when retries are exhausted.
    * ``strategy``: Normalized backoff strategy name.
    * ``exhausted``: ``True`` when no retries remain.

    Args:
        metadata: Raw retry metadata mapping (see
            :func:`parse_retry_metadata`).

    Returns:
        A dictionary summary of the retry metadata.

    Raises:
        ValueError: If ``metadata`` is malformed (see
            :func:`parse_retry_metadata`).
    """
    parsed = parse_retry_metadata(metadata)

    if parsed.exhausted:
        next_delay_seconds: float | None = None
    else:
        # The next attempt is the (attempts + 1)-th attempt.
        next_delay_seconds = compute_backoff_delay(
            attempt=parsed.attempts + 1,
            base_delay_seconds=parsed.base_delay_seconds,
            strategy=parsed.backoff_strategy,
        )

    return {
        "attempts": parsed.attempts,
        "remaining_retries": parsed.remaining_retries,
        "next_delay_seconds": next_delay_seconds,
        "strategy": parsed.backoff_strategy,
        "exhausted": parsed.exhausted,
    }