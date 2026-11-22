"""Unit tests for :mod:`app.sla_operations.utils.thresholds`.

These tests are intentionally isolated from core application state (no DB,
no FastAPI, no settings) and exercise only the pure helper functions.
"""

from __future__ import annotations

import math

import pytest

from app.sla_operations.utils.thresholds import (
    ThresholdComparison,
    compare_threshold,
    exceeds_threshold,
    is_within_threshold,
    meets_threshold,
    normalize_threshold,
    threshold_status,
)


class TestCompareThreshold:
    def test_below_threshold(self) -> None:
        assert compare_threshold(10.0, 20.0) is ThresholdComparison.BELOW

    def test_above_threshold(self) -> None:
        assert compare_threshold(30.0, 20.0) is ThresholdComparison.ABOVE

    def test_equal_is_at_threshold(self) -> None:
        assert compare_threshold(20.0, 20.0) is ThresholdComparison.AT

    def test_integer_inputs(self) -> None:
        assert compare_threshold(5, 5) is ThresholdComparison.AT
        assert compare_threshold(4, 5) is ThresholdComparison.BELOW
        assert compare_threshold(6, 5) is ThresholdComparison.ABOVE

    def test_negative_values(self) -> None:
        assert compare_threshold(-10.0, -5.0) is ThresholdComparison.BELOW
        assert compare_threshold(-1.0, -5.0) is ThresholdComparison.ABOVE

    def test_nan_observed_raises(self) -> None:
        with pytest.raises(ValueError):
            compare_threshold(float("nan"), 1.0)

    def test_nan_threshold_raises(self) -> None:
        with pytest.raises(ValueError):
            compare_threshold(1.0, float("nan"))

    def test_infinite_observed_raises(self) -> None:
        with pytest.raises(ValueError):
            compare_threshold(float("inf"), 1.0)

    def test_infinite_threshold_raises(self) -> None:
        with pytest.raises(ValueError):
            compare_threshold(1.0, float("-inf"))

    def test_non_numeric_raises(self) -> None:
        with pytest.raises(TypeError):
            compare_threshold("10", 1.0)  # type: ignore[arg-type]

    def test_bool_rejected(self) -> None:
        with pytest.raises(TypeError):
            compare_threshold(True, 1.0)  # type: ignore[arg-type]


class TestBooleanHelpers:
    def test_meets_threshold_inclusive(self) -> None:
        assert meets_threshold(20.0, 20.0) is True
        assert meets_threshold(19.999, 20.0) is False
        assert meets_threshold(20.001, 20.0) is True

    def test_exceeds_threshold_strict(self) -> None:
        assert exceeds_threshold(20.0, 20.0) is False
        assert exceeds_threshold(20.001, 20.0) is True
        assert exceeds_threshold(19.999, 20.0) is False

    def test_is_within_threshold_inclusive(self) -> None:
        assert is_within_threshold(20.0, 20.0) is True
        assert is_within_threshold(19.999, 20.0) is True
        assert is_within_threshold(20.001, 20.0) is False

    def test_helpers_propagate_validation_errors(self) -> None:
        for helper in (meets_threshold, exceeds_threshold, is_within_threshold):
            with pytest.raises(ValueError):
                helper(float("nan"), 1.0)
            with pytest.raises(ValueError):
                helper(1.0, float("inf"))


class TestThresholdStatus:
    def test_status_ok_when_below(self) -> None:
        assert threshold_status(10.0, 20.0) == "ok"

    def test_status_warning_at_boundary(self) -> None:
        assert threshold_status(20.0, 20.0) == "warning"

    def test_status_breach_when_above(self) -> None:
        assert threshold_status(25.0, 20.0) == "breach"

    def test_custom_labels(self) -> None:
        status = threshold_status(
            25.0,
            20.0,
            below_label="healthy",
            at_label="edge",
            above_label="critical",
        )
        assert status == "critical"

    def test_invalid_input_raises(self) -> None:
        with pytest.raises(ValueError):
            threshold_status(float("nan"), 20.0)


class TestNormalizeThreshold:
    def test_returns_float(self) -> None:
        result = normalize_threshold(10)
        assert isinstance(result, float)
        assert result == 10.0

    def test_accepts_float(self) -> None:
        assert normalize_threshold(2.5) == 2.5

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValueError):
            normalize_threshold(float("nan"))

    def test_rejects_infinity(self) -> None:
        with pytest.raises(ValueError):
            normalize_threshold(float("inf"))

    def test_rejects_bool(self) -> None:
        with pytest.raises(TypeError):
            normalize_threshold(True)  # type: ignore[arg-type]

    def test_rejects_non_numeric(self) -> None:
        with pytest.raises(TypeError):
            normalize_threshold("5")  # type: ignore[arg-type]

    def test_minimum_bound_enforced(self) -> None:
        with pytest.raises(ValueError):
            normalize_threshold(-1.0, minimum=0.0)

    def test_maximum_bound_enforced(self) -> None:
        with pytest.raises(ValueError):
            normalize_threshold(101.0, maximum=100.0)

    def test_bounds_inclusive(self) -> None:
        assert normalize_threshold(0.0, minimum=0.0, maximum=100.0) == 0.0
        assert normalize_threshold(100.0, minimum=0.0, maximum=100.0) == 100.0

    def test_invalid_bounds_raise(self) -> None:
        with pytest.raises(ValueError):
            normalize_threshold(5.0, minimum=10.0, maximum=1.0)

    def test_nan_bounds_raise(self) -> None:
        with pytest.raises(ValueError):
            normalize_threshold(5.0, minimum=float("nan"))

    def test_finite_result(self) -> None:
        assert math.isfinite(normalize_threshold(3.14))