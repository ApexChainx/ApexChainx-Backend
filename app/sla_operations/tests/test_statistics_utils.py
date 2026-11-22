"""Tests for the SLA operations workspace statistics utilities.

These tests exercise ``app.sla_operations.utils.statistics`` in isolation from
core application state. All numeric inputs are fixed literals so the suite is
fully deterministic.
"""

from __future__ import annotations

import math

import pytest

from app.sla_operations.utils import statistics as stats


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_values() -> list[float]:
    """A fixed, unsorted list of numeric values used across several tests."""
    return [4.0, 1.0, 3.0, 2.0, 5.0]


@pytest.fixture()
def single_value() -> list[float]:
    """A single-element list for degenerate-case testing."""
    return [42.0]


# ---------------------------------------------------------------------------
# Known-value correctness
# ---------------------------------------------------------------------------


def test_mean_known_value(sample_values: list[float]) -> None:
    # (4 + 1 + 3 + 2 + 5) / 5 == 3.0
    assert stats.mean(sample_values) == pytest.approx(3.0)


def test_median_known_value_odd(sample_values: list[float]) -> None:
    # Sorted: [1, 2, 3, 4, 5] -> median 3.0
    assert stats.median(sample_values) == pytest.approx(3.0)


def test_median_known_value_even() -> None:
    # Sorted: [1, 2, 3, 4] -> (2 + 3) / 2 == 2.5
    assert stats.median([4.0, 1.0, 3.0, 2.0]) == pytest.approx(2.5)


def test_percentile_known_value() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    # Linear interpolation: 50th percentile of 1..5 is 3.0
    assert stats.percentile(values, 50) == pytest.approx(3.0)
    # 0th percentile is the minimum, 100th is the maximum
    assert stats.percentile(values, 0) == pytest.approx(1.0)
    assert stats.percentile(values, 100) == pytest.approx(5.0)
    # 25th percentile: position 0.25 * (5 - 1) == 1.0 -> value 2.0
    assert stats.percentile(values, 25) == pytest.approx(2.0)


def test_stddev_known_value_population() -> None:
    # Population stddev of [2, 4, 4, 4, 5, 5, 7, 9] is 2.0
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    assert stats.stddev(values) == pytest.approx(2.0)


def test_stddev_known_value_sample() -> None:
    # Sample stddev of [2, 4, 4, 4, 5, 5, 7, 9] is sqrt(32/7)
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    expected = math.sqrt(32.0 / 7.0)
    assert stats.stddev(values, sample=True) == pytest.approx(expected)


def test_min_known_value(sample_values: list[float]) -> None:
    assert stats.minimum(sample_values) == pytest.approx(1.0)


def test_max_known_value(sample_values: list[float]) -> None:
    assert stats.maximum(sample_values) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Single-element input
# ---------------------------------------------------------------------------


def test_single_element_helpers(single_value: list[float]) -> None:
    assert stats.mean(single_value) == pytest.approx(42.0)
    assert stats.median(single_value) == pytest.approx(42.0)
    assert stats.minimum(single_value) == pytest.approx(42.0)
    assert stats.maximum(single_value) == pytest.approx(42.0)
    assert stats.percentile(single_value, 50) == pytest.approx(42.0)
    # Population stddev of a single value is 0.0
    assert stats.stddev(single_value) == pytest.approx(0.0)


def test_single_element_sample_stddev_raises(single_value: list[float]) -> None:
    # Sample stddev requires at least two observations.
    with pytest.raises(ValueError):
        stats.stddev(single_value, sample=True)


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func",
    [
        stats.mean,
        stats.median,
        stats.minimum,
        stats.maximum,
        stats.stddev,
    ],
)
def test_empty_input_raises_value_error(func) -> None:
    with pytest.raises(ValueError):
        func([])


def test_empty_input_percentile_raises_value_error() -> None:
    with pytest.raises(ValueError):
        stats.percentile([], 50)


# ---------------------------------------------------------------------------
# Invalid input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func",
    [
        stats.mean,
        stats.median,
        stats.minimum,
        stats.maximum,
        stats.stddev,
    ],
)
def test_non_numeric_values_raise_type_error(func) -> None:
    with pytest.raises(TypeError):
        func([1.0, "two", 3.0])  # type: ignore[list-item]


def test_non_numeric_percentile_raises_type_error() -> None:
    with pytest.raises(TypeError):
        stats.percentile([1.0, "two", 3.0], 50)  # type: ignore[list-item]


@pytest.mark.parametrize("bad_percentile", [-1, -0.5, 100.5, 101, 200])
def test_percentile_out_of_range_raises_value_error(bad_percentile: float) -> None:
    with pytest.raises(ValueError):
        stats.percentile([1.0, 2.0, 3.0], bad_percentile)


@pytest.mark.parametrize("bad_percentile", ["50", None])
def test_percentile_non_numeric_raises_type_error(bad_percentile) -> None:
    with pytest.raises(TypeError):
        stats.percentile([1.0, 2.0, 3.0], bad_percentile)


# ---------------------------------------------------------------------------
# Input isolation
# ---------------------------------------------------------------------------


def test_helpers_do_not_mutate_input(sample_values: list[float]) -> None:
    original = list(sample_values)
    stats.mean(sample_values)
    stats.median(sample_values)
    stats.percentile(sample_values, 50)
    stats.stddev(sample_values)
    stats.minimum(sample_values)
    stats.maximum(sample_values)
    assert sample_values == original