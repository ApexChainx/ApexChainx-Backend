"""Tests for the SLA operations ratio/percentage helpers.

These tests exercise ``app.sla_operations.utils.ratios`` in isolation from
core application state. They are deterministic and rely only on plain
pytest assertions.
"""

from __future__ import annotations

import pytest

from app.sla_operations.utils.ratios import (
    percentage,
    ratio,
    safe_divide,
    average,
    weighted_average,
)


# ---------------------------------------------------------------------------
# safe_divide
# ---------------------------------------------------------------------------


class TestSafeDivide:
    def test_normal_division(self) -> None:
        assert safe_divide(10, 2) == 5.0

    def test_returns_float(self) -> None:
        assert isinstance(safe_divide(3, 2), float)

    def test_zero_denominator_returns_default(self) -> None:
        assert safe_divide(10, 0) == 0.0

    def test_zero_denominator_custom_default(self) -> None:
        assert safe_divide(10, 0, default=-1.0) == -1.0

    def test_zero_numerator(self) -> None:
        assert safe_divide(0, 5) == 0.0

    def test_zero_over_zero_returns_default(self) -> None:
        assert safe_divide(0, 0) == 0.0

    def test_negative_values(self) -> None:
        assert safe_divide(-10, 2) == -5.0
        assert safe_divide(10, -2) == -5.0
        assert safe_divide(-10, -2) == 5.0

    @pytest.mark.parametrize(
        "numerator,denominator,expected",
        [
            (1, 4, 0.25),
            (1, 3, 1 / 3),
            (100, 8, 12.5),
            (0, 1, 0.0),
        ],
    )
    def test_parametrized(
        self, numerator: float, denominator: float, expected: float
    ) -> None:
        assert safe_divide(numerator, denominator) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# ratio
# ---------------------------------------------------------------------------


class TestRatio:
    def test_normal_ratio(self) -> None:
        assert ratio(1, 4) == pytest.approx(0.25)

    def test_full_ratio(self) -> None:
        assert ratio(4, 4) == pytest.approx(1.0)

    def test_zero_denominator_returns_zero(self) -> None:
        assert ratio(5, 0) == 0.0

    def test_zero_numerator(self) -> None:
        assert ratio(0, 5) == 0.0

    def test_zero_over_zero(self) -> None:
        assert ratio(0, 0) == 0.0

    def test_ratio_greater_than_one(self) -> None:
        assert ratio(10, 4) == pytest.approx(2.5)

    def test_negative_denominator(self) -> None:
        assert ratio(5, -5) == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# percentage
# ---------------------------------------------------------------------------


class TestPercentage:
    def test_normal_percentage(self) -> None:
        assert percentage(1, 4) == pytest.approx(25.0)

    def test_zero_percent(self) -> None:
        assert percentage(0, 10) == pytest.approx(0.0)

    def test_hundred_percent(self) -> None:
        assert percentage(10, 10) == pytest.approx(100.0)

    def test_over_hundred_percent(self) -> None:
        assert percentage(15, 10) == pytest.approx(150.0)

    def test_zero_denominator_returns_zero(self) -> None:
        assert percentage(5, 0) == 0.0

    def test_zero_over_zero(self) -> None:
        assert percentage(0, 0) == 0.0

    @pytest.mark.parametrize(
        "part,whole,expected",
        [
            (1, 2, 50.0),
            (1, 3, 100.0 / 3.0),
            (2, 8, 25.0),
            (7, 10, 70.0),
            (0, 100, 0.0),
            (100, 100, 100.0),
            (200, 100, 200.0),
        ],
    )
    def test_parametrized(
        self, part: float, whole: float, expected: float
    ) -> None:
        assert percentage(part, whole) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# average
# ---------------------------------------------------------------------------


class TestAverage:
    def test_normal_average(self) -> None:
        assert average([1, 2, 3, 4]) == pytest.approx(2.5)

    def test_single_value(self) -> None:
        assert average([42]) == pytest.approx(42.0)

    def test_empty_sequence_returns_zero(self) -> None:
        assert average([]) == 0.0

    def test_empty_tuple_returns_zero(self) -> None:
        assert average(()) == 0.0

    def test_all_zeros(self) -> None:
        assert average([0, 0, 0]) == 0.0

    def test_negative_values(self) -> None:
        assert average([-1, -2, -3]) == pytest.approx(-2.0)

    def test_mixed_values(self) -> None:
        assert average([-10, 0, 10]) == pytest.approx(0.0)

    def test_accepts_generator(self) -> None:
        assert average(x for x in (2, 4, 6)) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# weighted_average
# ---------------------------------------------------------------------------


class TestWeightedAverage:
    def test_normal_weighted_average(self) -> None:
        assert weighted_average([1, 2, 3], [1, 1, 1]) == pytest.approx(2.0)

    def test_weighted_average_with_weights(self) -> None:
        # (1*1 + 2*2 + 3*3) / (1 + 2 + 3) = 14 / 6
        assert weighted_average([1, 2, 3], [1, 2, 3]) == pytest.approx(14 / 6)

    def test_zero_total_weight_returns_zero(self) -> None:
        assert weighted_average([1, 2, 3], [0, 0, 0]) == 0.0

    def test_empty_sequences_return_zero(self) -> None:
        assert weighted_average([], []) == 0.0

    def test_single_value(self) -> None:
        assert weighted_average([10], [5]) == pytest.approx(10.0)

    def test_mismatched_lengths_raise(self) -> None:
        with pytest.raises(ValueError):
            weighted_average([1, 2, 3], [1, 2])

    def test_negative_weights(self) -> None:
        # (1*-1 + 3*1) / (-1 + 1) -> zero total weight
        assert weighted_average([1, 3], [-1, 1]) == 0.0

    def test_negative_values(self) -> None:
        assert weighted_average([-1, -3], [1, 1]) == pytest.approx(-2.0)