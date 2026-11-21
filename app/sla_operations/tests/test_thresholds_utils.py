"""Tests for SLA operations threshold comparison utilities.

These tests exercise ``app.sla_operations.utils.thresholds`` in isolation from
core application state. They cover boundary comparisons, delta classification
(with and without tolerance), and error handling for invalid inputs.
"""

from __future__ import annotations

import pytest

from app.sla_operations.utils.thresholds import (
    DeltaClassification,
    classify_delta,
    compare_to_threshold,
)


class TestCompareToThreshold:
    """Tests for ``compare_to_threshold``."""

    def test_value_above_threshold(self) -> None:
        assert compare_to_threshold(10.0, 5.0) == 1

    def test_value_below_threshold(self) -> None:
        assert compare_to_threshold(2.0, 5.0) == -1

    def test_value_equal_to_threshold(self) -> None:
        assert compare_to_threshold(5.0, 5.0) == 0

    def test_exact_boundary_with_integers(self) -> None:
        assert compare_to_threshold(100, 100) == 0
        assert compare_to_threshold(101, 100) == 1
        assert compare_to_threshold(99, 100) == -1

    def test_exact_boundary_with_floats(self) -> None:
        assert compare_to_threshold(0.1 + 0.2, 0.3) == 0
        assert compare_to_threshold(0.3000001, 0.3) == 1
        assert compare_to_threshold(0.2999999, 0.3) == -1

    def test_negative_values(self) -> None:
        assert compare_to_threshold(-5.0, -10.0) == 1
        assert compare_to_threshold(-15.0, -10.0) == -1
        assert compare_to_threshold(-10.0, -10.0) == 0

    def test_zero_threshold(self) -> None:
        assert compare_to_threshold(0.0, 0.0) == 0
        assert compare_to_threshold(1.0, 0.0) == 1
        assert compare_to_threshold(-1.0, 0.0) == -1

    def test_invalid_value_type_raises(self) -> None:
        with pytest.raises(TypeError):
            compare_to_threshold("not-a-number", 5.0)  # type: ignore[arg-type]

    def test_invalid_threshold_type_raises(self) -> None:
        with pytest.raises(TypeError):
            compare_to_threshold(5.0, None)  # type: ignore[arg-type]

    def test_nan_value_raises(self) -> None:
        with pytest.raises(ValueError):
            compare_to_threshold(float("nan"), 5.0)

    def test_nan_threshold_raises(self) -> None:
        with pytest.raises(ValueError):
            compare_to_threshold(5.0, float("nan"))


class TestClassifyDelta:
    """Tests for ``classify_delta``."""

    def test_within_tolerance(self) -> None:
        assert classify_delta(100.0, 100.0, tolerance=5.0) == DeltaClassification.WITHIN

    def test_within_tolerance_at_upper_boundary(self) -> None:
        assert classify_delta(105.0, 100.0, tolerance=5.0) == DeltaClassification.WITHIN

    def test_within_tolerance_at_lower_boundary(self) -> None:
        assert classify_delta(95.0, 100.0, tolerance=5.0) == DeltaClassification.WITHIN

    def test_over_tolerance(self) -> None:
        assert classify_delta(106.0, 100.0, tolerance=5.0) == DeltaClassification.OVER

    def test_under_tolerance(self) -> None:
        assert classify_delta(94.0, 100.0, tolerance=5.0) == DeltaClassification.UNDER

    def test_without_tolerance_exact_match(self) -> None:
        assert classify_delta(100.0, 100.0) == DeltaClassification.WITHIN

    def test_without_tolerance_over(self) -> None:
        assert classify_delta(100.0001, 100.0) == DeltaClassification.OVER

    def test_without_tolerance_under(self) -> None:
        assert classify_delta(99.9999, 100.0) == DeltaClassification.UNDER

    def test_zero_tolerance_exact_match(self) -> None:
        assert classify_delta(50.0, 50.0, tolerance=0.0) == DeltaClassification.WITHIN

    def test_zero_tolerance_over(self) -> None:
        assert classify_delta(50.5, 50.0, tolerance=0.0) == DeltaClassification.OVER

    def test_zero_tolerance_under(self) -> None:
        assert classify_delta(49.5, 50.0, tolerance=0.0) == DeltaClassification.UNDER

    def test_negative_tolerance_raises(self) -> None:
        with pytest.raises(ValueError):
            classify_delta(100.0, 100.0, tolerance=-1.0)

    def test_nan_value_raises(self) -> None:
        with pytest.raises(ValueError):
            classify_delta(float("nan"), 100.0, tolerance=5.0)

    def test_nan_target_raises(self) -> None:
        with pytest.raises(ValueError):
            classify_delta(100.0, float("nan"), tolerance=5.0)

    def test_invalid_value_type_raises(self) -> None:
        with pytest.raises(TypeError):
            classify_delta("bad", 100.0, tolerance=5.0)  # type: ignore[arg-type]

    def test_invalid_tolerance_type_raises(self) -> None:
        with pytest.raises(TypeError):
            classify_delta(100.0, 100.0, tolerance="bad")  # type: ignore[arg-type]

    def test_negative_values(self) -> None:
        assert classify_delta(-100.0, -100.0, tolerance=5.0) == DeltaClassification.WITHIN
        assert classify_delta(-94.0, -100.0, tolerance=5.0) == DeltaClassification.OVER
        assert classify_delta(-106.0, -100.0, tolerance=5.0) == DeltaClassification.UNDER