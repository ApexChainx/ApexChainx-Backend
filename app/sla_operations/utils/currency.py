from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Optional, Union

# Default quantization for monetary amounts (two decimal places).
DEFAULT_QUANTUM = Decimal("0.01")

# Rounding mode used throughout this module. ROUND_HALF_UP rounds away from
# zero on ties (e.g. 0.125 -> 0.13, -0.125 -> -0.13), which matches the
# conventional expectation for monetary values.
ROUNDING_MODE = ROUND_HALF_UP

# Type alias for values accepted by the helpers in this module.
AmountLike = Union[Decimal, int, float, str]


def _to_decimal(value: AmountLike) -> Decimal:
    """Convert an input value to a ``Decimal``.

    Accepts ``Decimal``, ``int``, ``float``, and ``str`` inputs. Floats are
    converted via their string representation to avoid binary floating point
    artifacts. ``bool`` is rejected even though it is a subclass of ``int``.

    Raises:
        TypeError: If ``value`` is not a supported numeric type.
        ValueError: If ``value`` is a string that cannot be parsed as a number,
            or is a non-finite ``Decimal``/``float`` (NaN or Infinity).
    """
    if isinstance(value, bool):
        raise TypeError("Boolean values are not valid monetary amounts")

    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"Invalid non-finite amount: {value!r}")
        result = Decimal(str(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Invalid amount string: {value!r}") from exc
    else:
        raise TypeError(
            f"Unsupported amount type: {type(value).__name__!r}; "
            "expected Decimal, int, float, or str"
        )

    if not result.is_finite():
        raise ValueError(f"Invalid non-finite amount: {value!r}")

    return result


def normalize_amount(
    value: AmountLike,
    quantum: Decimal = DEFAULT_QUANTUM,
) -> Decimal:
    """Normalize a monetary amount to a fixed-precision ``Decimal``.

    The value is converted to ``Decimal`` and quantized to ``quantum`` using
    ``ROUND_HALF_UP``. This is the canonical entry point for turning arbitrary
    input into a deterministic monetary value.

    Args:
        value: The amount to normalize.
        quantum: The target precision (default ``Decimal('0.01')``).

    Returns:
        The normalized ``Decimal`` amount.

    Raises:
        TypeError: If ``value`` or ``quantum`` is of an unsupported type.
        ValueError: If ``value`` cannot be parsed or ``quantum`` is invalid.
    """
    if not isinstance(quantum, Decimal):
        raise TypeError(
            f"quantum must be a Decimal, got {type(quantum).__name__!r}"
        )
    if not quantum.is_finite() or quantum <= 0:
        raise ValueError(f"quantum must be a positive finite Decimal: {quantum!r}")

    amount = _to_decimal(value)
    return amount.quantize(quantum, rounding=ROUNDING_MODE)


def round_amount(
    value: AmountLike,
    quantum: Decimal = DEFAULT_QUANTUM,
) -> Decimal:
    """Round a monetary amount to ``quantum`` using ``ROUND_HALF_UP``.

    This is a thin, explicitly-named wrapper around :func:`normalize_amount`
    for callers that want to signal rounding intent.

    Raises:
        TypeError: If ``value`` or ``quantum`` is of an unsupported type.
        ValueError: If ``value`` cannot be parsed or ``quantum`` is invalid.
    """
    return normalize_amount(value, quantum=quantum)


def format_amount(
    value: AmountLike,
    currency: Optional[str] = None,
    quantum: Decimal = DEFAULT_QUANTUM,
) -> str:
    """Format a monetary amount as a human-readable string.

    The amount is normalized to ``quantum`` and rendered with a fixed number
    of decimal places. If ``currency`` is provided, it is prefixed followed by
    a single space (e.g. ``"USD 12.35"``).

    Args:
        value: The amount to format.
        currency: Optional currency code or symbol to prefix.
        quantum: The target precision (default ``Decimal('0.01')``).

    Returns:
        The formatted string.

    Raises:
        TypeError: If ``value``, ``currency``, or ``quantum`` is invalid.
        ValueError: If ``value`` cannot be parsed or ``quantum`` is invalid.
    """
    if currency is not None and not isinstance(currency, str):
        raise TypeError(
            f"currency must be a string or None, got {type(currency).__name__!r}"
        )

    normalized = normalize_amount(value, quantum=quantum)

    # Determine the number of decimal places implied by the quantum.
    exponent = quantum.normalize().as_tuple().exponent
    decimals = -exponent if isinstance(exponent, int) and exponent < 0 else 0

    formatted = f"{normalized:.{decimals}f}"

    if currency:
        return f"{currency.strip()} {formatted}"
    return formatted


def sum_amounts(
    values: Iterable[AmountLike],
    quantum: Decimal = DEFAULT_QUANTUM,
) -> Decimal:
    """Sum an iterable of monetary amounts, returning a normalized ``Decimal``.

    Each value is normalized before summing so the result is deterministic.
    An empty iterable yields ``Decimal('0.00')`` (i.e. zero quantized to
    ``quantum``).

    Args:
        values: An iterable of amounts to sum.
        quantum: The target precision (default ``Decimal('0.01')``).

    Returns:
        The normalized sum as a ``Decimal``.

    Raises:
        TypeError: If ``values`` is not iterable or contains unsupported types.
        ValueError: If any value cannot be parsed or ``quantum`` is invalid.
    """
    if not isinstance(quantum, Decimal):
        raise TypeError(
            f"quantum must be a Decimal, got {type(quantum).__name__!r}"
        )
    if not quantum.is_finite() or quantum <= 0:
        raise ValueError(f"quantum must be a positive finite Decimal: {quantum!r}")

    total = Decimal("0")
    for value in values:
        total += normalize_amount(value, quantum=quantum)

    return total.quantize(quantum, rounding=ROUNDING_MODE)
</parameter>