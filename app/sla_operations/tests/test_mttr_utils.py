"""Tests for MTTR utility calculations and formatting behaviors.

These tests are isolated from core application state and exercise only the
workspace-local ``app.sla_operations.utils.mttr`` module.
"""

from __future__ import annotations

import pytest

from app.sla_operations.utils.mttr import calculate_mttr, format_duration


class TestCalculateMttr:
    """Tests for ``calculate_mttr``."""

    def test_mean_of_multiple_durations(self) -> None:
        """MTTR is the arithmetic mean of the provided durations."""
        assert calculate_mttr([10, 20, 30]) == 20.0

    def test_mean_with_floats(self) -> None:
        """Floating point durations are averaged correctly."""
        assert calculate_mttr([1.5, 2.5, 3.5]) == pytest.approx(2.5)

    def test_single_value_returns_that_value(self) -> None:
        """A single duration yields itself as the mean."""
        assert calculate_mttr([42]) == 42.0

    def test_single_zero_value(self) -> None:
        """A single zero duration yields zero."""
        assert calculate_mttr([0]) == 0.0

    def test_empty_input_returns_zero(self) -> None:
        """An empty sequence yields a zero MTTR rather than raising."""
        assert calculate_mttr([]) == 0.0

    def test_zero_durations_are_included(self) -> None:
        """Zero durations are valid and participate in the mean."""
        assert calculate_mttr([0, 0, 0]) == 0.0
        assert calculate_mttr([0, 10]) == 5.0

    def test_negative_duration_raises_value_error(self) -> None:
        """Negative durations are invalid and raise ``ValueError``."""
        with pytest.raises(ValueError):
            calculate_mttr([10, -5, 20])

    def test_negative_duration_raises_even_when_alone(self) -> None:
        """A single negative duration also raises ``ValueError``."""
        with pytest.raises(ValueError):
            calculate_mttr([-1])

    def test_accepts_tuple_input(self) -> None:
        """Any iterable of durations is accepted."""
        assert calculate_mttr((5, 15)) == 10.0


class TestFormatDuration:
    """Tests for ``format_duration``."""

    def test_formats_seconds(self) -> None:
        """Durations under a minute are rendered in seconds."""
        assert format_duration(45) == "45s"

    def test_formats_zero_seconds(self) -> None:
        """Zero renders as zero seconds."""
        assert format_duration(0) == "0s"

    def test_formats_minutes(self) -> None:
        """Durations of at least a minute are rendered in minutes."""
        assert format_duration(120) == "2m"

    def test_formats_hours(self) -> None:
        """Durations of at least an hour are rendered in hours."""
        assert format_duration(7200) == "2h"

    def test_formats_mixed_duration(self) -> None:
        """Mixed durations render the largest whole unit with remainder."""
        assert format_duration(3661) == "1h 1m 1s"

    def test_formats_float_duration(self) -> None:
        """Float durations are truncated to whole seconds for display."""
        assert format_duration(90.9) == "1m 30s"

    def test_negative_duration_raises_value_error(self) -> None:
        """Negative durations cannot be formatted."""
        with pytest.raises(ValueError):
            format_duration(-1)