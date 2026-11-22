"""Tests for SLA operations workspace currency utilities.

These tests exercise ``app.sla_operations.utils.currency`` in isolation from
core application state. All expected values are expressed with ``Decimal`` to
avoid floating point ambiguity.
"""

from decimal import Decimal

import pytest

from app.sla_operations.utils.currency import (
    aggregate_amounts,
    format_amount,
    normalize_amount,
    round_amount,
)


# ---------------------------------------------------------------------------
# normalize_amount
# ---------------------------------------------------------------------------


class TestNormalizeAmount:
    def test_normalize_int(self):
        assert normalize_amount(100) == Decimal("100")

    def test_normalize_zero_int(self):
        assert normalize_amount(0) == Decimal("0")

    def test_normalize_negative_int(self):
        assert normalize_amount(-42) == Decimal("-42")

    def test_normalize_decimal(self):
        assert normalize_amount(Decimal("12.345")) == Decimal("12.345")

    def test_normalize_string(self):
        assert normalize_amount("19.99") == Decimal("19.99")

    def test_normalize_string_with_whitespace(self):
        assert normalize_amount("  7.50  ") == Decimal("7.50")

    def test_normalize_string_with_currency_symbol(self):
        assert normalize_amount("$1,234.56") == Decimal("1234.56")

    def test_normalize_negative_string(self):
        assert normalize_amount("-3.14") == Decimal("-3.14")

    def test_normalize_float_uses_string_conversion(self):
        # Floats are converted via their string representation to avoid
        # binary floating point artifacts.
        assert normalize_amount(0.1) == Decimal("0.1")

    def test_normalize_none_raises(self):
        with pytest.raises((TypeError, ValueError)):
            normalize_amount(None)

    def test_normalize_invalid_string_raises(self):
        with pytest.raises(ValueError):
            normalize_amount("not-a-number")

    def test_normalize_empty_string_raises(self):
        with pytest.raises(ValueError):
            normalize_amount("")

    def test_normalize_unsupported_type_raises(self):
        with pytest.raises((TypeError, ValueError)):
            normalize_amount([1, 2, 3])


# ---------------------------------------------------------------------------
# round_amount
# ---------------------------------------------------------------------------


class TestRoundAmount:
    def test_round_default_quantum(self):
        assert round_amount(Decimal("1.234")) == Decimal("1.23")

    def test_round_half_up_positive(self):
        # Half-up: 1.235 -> 1.24 (not banker's rounding to 1.24/1.23).
        assert round_amount(Decimal("1.235")) == Decimal("1.24")

    def test_round_half_up_negative(self):
        # Half-up away from zero for negatives: -1.235 -> -1.24.
        assert round_amount(Decimal("-1.235")) == Decimal("-1.24")

    def test_round_half_up_exact_half(self):
        assert round_amount(Decimal("2.5"), places=0) == Decimal("3")

    def test_round_half_up_exact_half_negative(self):
        assert round_amount(Decimal("-2.5"), places=0) == Decimal("-3")

    def test_round_below_half_rounds_down(self):
        assert round_amount(Decimal("1.234")) == Decimal("1.23")

    def test_round_above_half_rounds_up(self):
        assert round_amount(Decimal("1.236")) == Decimal("1.24")

    def test_round_custom_places(self):
        assert round_amount(Decimal("1.23456"), places=4) == Decimal("1.2346")

    def test_round_zero_places(self):
        assert round_amount(Decimal("9.99"), places=0) == Decimal("10")

    def test_round_accepts_string_input(self):
        assert round_amount("1.235") == Decimal("1.24")

    def test_round_accepts_int_input(self):
        assert round_amount(5) == Decimal("5.00")

    def test_round_negative_places_raises(self):
        with pytest.raises(ValueError):
            round_amount(Decimal("1.23"), places=-1)

    def test_round_invalid_input_raises(self):
        with pytest.raises(ValueError):
            round_amount("abc")


# ---------------------------------------------------------------------------
# format_amount
# ---------------------------------------------------------------------------


class TestFormatAmount:
    def test_format_default(self):
        assert format_amount(Decimal("1234.5")) == "1234.50"

    def test_format_rounds_half_up(self):
        assert format_amount(Decimal("1.235")) == "1.24"

    def test_format_zero(self):
        assert format_amount(Decimal("0")) == "0.00"

    def test_format_negative(self):
        assert format_amount(Decimal("-12.345")) == "-12.35"

    def test_format_with_currency_symbol(self):
        assert format_amount(Decimal("1234.5"), symbol="$") == "$1234.50"

    def test_format_with_thousands_separator(self):
        assert format_amount(Decimal("1234567.891"), thousands=True) == "1,234,567.89"

    def test_format_with_symbol_and_thousands(self):
        assert (
            format_amount(Decimal("1234567.891"), symbol="$", thousands=True)
            == "$1,234,567.89"
        )

    def test_format_custom_places(self):
        assert format_amount(Decimal("1.23456"), places=4) == "1.2346"

    def test_format_accepts_string_input(self):
        assert format_amount("42.1") == "42.10"

    def test_format_accepts_int_input(self):
        assert format_amount(7) == "7.00"

    def test_format_invalid_input_raises(self):
        with pytest.raises(ValueError):
            format_amount("not-a-number")

    def test_format_negative_places_raises(self):
        with pytest.raises(ValueError):
            format_amount(Decimal("1.23"), places=-1)


# ---------------------------------------------------------------------------
# aggregate_amounts
# ---------------------------------------------------------------------------


class TestAggregateAmounts:
    def test_aggregate_empty_returns_zero(self):
        assert aggregate_amounts([]) == Decimal("0")

    def test_aggregate_single_value(self):
        assert aggregate_amounts([Decimal("10.00")]) == Decimal("10.00")

    def test_aggregate_mixed_inputs(self):
        result = aggregate_amounts([1, "2.50", Decimal("3.25")])
        assert result == Decimal("6.75")

    def test_aggregate_with_negatives(self):
        result = aggregate_amounts([Decimal("10.00"), Decimal("-2.50")])
        assert result == Decimal("7.50")

    def test_aggregate_strings_with_symbols(self):
        result = aggregate_amounts(["$1,000.00", "250.50"])
        assert result == Decimal("1250.50")

    def test_aggregate_returns_decimal(self):
        result = aggregate_amounts([1, 2, 3])
        assert isinstance(result, Decimal)
        assert result == Decimal("6")

    def test_aggregate_invalid_entry_raises(self):
        with pytest.raises(ValueError):
            aggregate_amounts([Decimal("1.00"), "bad-value"])

    def test_aggregate_none_entry_raises(self):
        with pytest.raises((TypeError, ValueError)):
            aggregate_amounts([Decimal("1.00"), None])