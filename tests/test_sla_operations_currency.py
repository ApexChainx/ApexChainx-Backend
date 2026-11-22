"""Unit tests for the SLA operations currency utilities.

These tests are intentionally isolated from the core application state and
exercise only the pure helpers in ``app.sla_operations.utils.currency``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.sla_operations.utils.currency import (
    format_amount,
    normalize_amount,
    sum_amounts,
)


class TestNormalizeAmount:
    """Tests for :func:`normalize_amount`."""

    def test_accepts_int(self) -> None:
        assert normalize_amount(100) == Decimal("100")

    def test_accepts_negative_int(self) -> None:
        assert normalize_amount(-42) == Decimal("-42")

    def test_accepts_zero(self) -> None:
        assert normalize_amount(0) == Decimal("0")

    def test_accepts_str(self) -> None:
        assert normalize_amount("123.45") == Decimal("123.45")

    def test_accepts_negative_str(self) -> None:
        assert normalize_amount("-0.01") == Decimal("-0.01")

    def test_accepts_decimal(self) -> None:
        assert normalize_amount(Decimal("99.99")) == Decimal("99.99")

    def test_accepts_float(self) -> None:
        assert normalize_amount(1.5) == Decimal("1.5")

    def test_strips_whitespace_in_str(self) -> None:
        assert normalize_amount("  10.00  ") == Decimal("10.00")

    def test_quantizes_to_default_places(self) -> None:
        assert normalize_amount("1.005") == Decimal("1.00")

    def test_quantizes_to_custom_places(self) -> None:
        assert normalize_amount("1.23456", places=4) == Decimal("1.2346")

    def test_rounds_half_up(self) -> None:
        assert normalize_amount("2.345", places=2) == Decimal("2.35")

    def test_returns_decimal_type(self) -> None:
        assert isinstance(normalize_amount("5"), Decimal)

    @pytest.mark.parametrize("value", ["", "   ", "abc", "1.2.3", "$5", "1,000"])
    def test_rejects_invalid_strings(self, value: str) -> None:
        with pytest.raises(ValueError):
            normalize_amount(value)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_non_finite_floats(self, value: float) -> None:
        with pytest.raises(ValueError):
            normalize_amount(value)

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "inf"])
    def test_rejects_non_finite_strings(self, value: str) -> None:
        with pytest.raises(ValueError):
            normalize_amount(value)

    def test_rejects_none(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            normalize_amount(None)  # type: ignore[arg-type]

    def test_rejects_bool(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            normalize_amount(True)  # type: ignore[arg-type]

    def test_rejects_negative_places(self) -> None:
        with pytest.raises(ValueError):
            normalize_amount("1.00", places=-1)


class TestSumAmounts:
    """Tests for :func:`sum_amounts`."""

    def test_sums_mixed_inputs(self) -> None:
        result = sum_amounts([1, "2.50", Decimal("3.25")])
        assert result == Decimal("6.75")

    def test_empty_iterable_returns_zero(self) -> None:
        assert sum_amounts([]) == Decimal("0")

    def test_empty_iterable_returns_zero_with_places(self) -> None:
        assert sum_amounts([], places=2) == Decimal("0.00")

    def test_sums_negative_values(self) -> None:
        assert sum_amounts([10, -3, "-2.50"]) == Decimal("4.50")

    def test_all_negative(self) -> None:
        assert sum_amounts(["-1.00", "-2.00"]) == Decimal("-3.00")

    def test_accepts_generator(self) -> None:
        assert sum_amounts(x for x in ["1", "2", "3"]) == Decimal("6")

    def test_quantizes_result(self) -> None:
        assert sum_amounts(["1.005", "0"], places=2) == Decimal("1.00")

    def test_returns_decimal_type(self) -> None:
        assert isinstance(sum_amounts([1, 2]), Decimal)

    def test_propagates_invalid_input(self) -> None:
        with pytest.raises(ValueError):
            sum_amounts(["1.00", "not-a-number"])

    def test_rejects_non_finite_input(self) -> None:
        with pytest.raises(ValueError):
            sum_amounts([1, float("nan")])


class TestFormatAmount:
    """Tests for :func:`format_amount`."""

    def test_formats_int(self) -> None:
        assert format_amount(100) == "100.00"

    def test_formats_str(self) -> None:
        assert format_amount("12.5") == "12.50"

    def test_formats_decimal(self) -> None:
        assert format_amount(Decimal("3.14159")) == "3.14"

    def test_rounds_half_up(self) -> None:
        assert format_amount("2.345") == "2.35"

    def test_rounds_down(self) -> None:
        assert format_amount("2.344") == "2.34"

    def test_negative_value(self) -> None:
        assert format_amount("-5.5") == "-5.50"

    def test_zero(self) -> None:
        assert format_amount(0) == "0.00"

    def test_custom_places(self) -> None:
        assert format_amount("1.23456", places=4) == "1.2346"

    def test_zero_places(self) -> None:
        assert format_amount("1.6", places=0) == "2"

    def test_includes_currency_symbol(self) -> None:
        assert format_amount("10", currency="USD") == "USD 10.00"

    def test_includes_currency_code_suffix(self) -> None:
        assert format_amount("10", currency="USD", suffix=True) == "10.00 USD"

    def test_no_currency_when_none(self) -> None:
        assert format_amount("10", currency=None) == "10.00"

    def test_returns_str_type(self) -> None:
        assert isinstance(format_amount("1"), str)

    def test_propagates_invalid_input(self) -> None:
        with pytest.raises(ValueError):
            format_amount("abc")

    def test_rejects_non_finite_input(self) -> None:
        with pytest.raises(ValueError):
            format_amount(float("inf"))