"""Currency amount utilities for the SLA operations workspace.

This module provides small, pure, dependency-light helpers for normalizing,
summing, and formatting monetary amounts. It is intentionally independent of
core application state (no DB, no settings, no logging) so it can be reused
freely by workspace services and schemas.

All monetary values are represented as :class:`decimal.Decimal` to avoid
binary floating-point rounding errors. Inputs may be ``int``, ``str``, or
``Decimal``; ``float`` inputs are rejected because they cannot represent most
decimal monetary values exactly.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Optional, Union

__all__ = [
    "AmountInput",
    "normalize_amount",
    "sum_amounts",
    "format_amount",
]

#: Accepted input types for monetary amount helpers.
AmountInput = Union[int, str, Decimal]


def normalize_amount(value: AmountInput) -> Decimal:
    """Normalize a monetary amount into a finite :class:`Decimal`.

    Parameters
    ----------
    value:
        The amount to normalize. Accepted types are ``int``, ``str``, and
        ``Decimal``. ``bool`` is explicitly rejected even though it is a
        subclass of ``int``. ``float`` is rejected because it cannot reliably
        represent decimal monetary values.

    Returns
    -------
    Decimal
        A finite ``Decimal`` equal to the input value. The returned value is
        not rounded or quantized; callers that need a fixed number of decimal
        places should use :func:`format_amount`.

    Raises
    ------
    ValueError
        If ``value`` is a ``bool``, a ``float``, an unsupported type, a string
        that cannot be parsed as a decimal number, or a non-finite value
        (``NaN``, ``Infinity``, ``-Infinity``).
    """
    if isinstance(value, bool):
        raise ValueError("Boolean values are not valid monetary amounts")

    if isinstance(value, float):
        raise ValueError(
            "Float values are not supported for monetary amounts; "
            "use int, str, or Decimal instead"
        )

    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Empty string is not a valid monetary amount")
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(
                f"Invalid monetary amount string: {value!r}"
            ) from exc
    else:
        raise ValueError(
            f"Unsupported monetary amount type: {type(value).__name__}"
        )

    if not amount.is_finite():
        raise ValueError(f"Monetary amount must be finite, got: {amount!r}")

    return amount


def sum_amounts(values: Iterable[AmountInput]) -> Decimal:
    """Sum an iterable of monetary amounts.

    Parameters
    ----------
    values:
        An iterable of amounts, each accepted by :func:`normalize_amount`.

    Returns
    -------
    Decimal
        The sum of the normalized amounts. Returns ``Decimal("0")`` for an
        empty iterable. Summation is performed left-to-right over the
        normalized ``Decimal`` values, which is deterministic for a given
        input order.

    Raises
    ------
    ValueError
        If any element is not a valid monetary amount (see
        :func:`normalize_amount`).
    """
    total = Decimal("0")
    for value in values:
        total += normalize_amount(value)
    return total


def format_amount(
    value: AmountInput,
    decimals: int = 2,
    currency: Optional[str] = None,
) -> str:
    """Format a monetary amount as a string with fixed decimal places.

    The amount is normalized via :func:`normalize_amount` and then quantized
    to ``decimals`` decimal places using ``ROUND_HALF_UP`` rounding (i.e.
    ``0.005`` at two decimals becomes ``"0.01"`` and ``-0.005`` becomes
    ``"-0.01"``).

    Parameters
    ----------
    value:
        The amount to format, accepted by :func:`normalize_amount`.
    decimals:
        Number of decimal places to render. Must be a non-negative ``int``
        (``bool`` is rejected). Defaults to ``2``.
    currency:
        Optional currency prefix (for example ``"USD"``). When provided, the
        output is ``"<currency> <amount>"``. Surrounding whitespace is
        stripped; an empty or whitespace-only string is treated as ``None``.

    Returns
    -------
    str
        The formatted amount, optionally prefixed with the currency code.

    Raises
    ------
    ValueError
        If ``value`` is not a valid monetary amount, if ``decimals`` is not a
        non-negative integer, or if ``currency`` is not a string or ``None``.
    """
    if isinstance(decimals, bool) or not isinstance(decimals, int):
        raise ValueError("decimals must be a non-negative integer")
    if decimals < 0:
        raise ValueError("decimals must be a non-negative integer")

    if currency is not None and not isinstance(currency, str):
        raise ValueError("currency must be a string or None")

    amount = normalize_amount(value)
    quantizer = Decimal(1).scaleb(-decimals)
    rounded = amount.quantize(quantizer, rounding=ROUND_HALF_UP)
    formatted = f"{rounded:.{decimals}f}"

    if currency is not None:
        prefix = currency.strip()
        if prefix:
            return f"{prefix} {formatted}"

    return formatted