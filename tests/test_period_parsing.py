"""Tests for SLAOrchestrator.parse_period — Issue #95.

Validates that the strict regex supports any year for monthly and quarterly
formats, and raises typed validation errors on bad input.
"""

from datetime import datetime

import pytest

from app.core.exceptions import ApexValidationError
from app.services.sla_service import SLAOrchestrator


def _make_orchestrator():
    """Create an SLAOrchestrator with no DB session for unit tests."""
    return SLAOrchestrator(db=None)


class TestParsePeriodMonthly:
    def test_2025_03_resolves(self):
        """Issue #95: 2025-03 must resolve without error."""
        orch = _make_orchestrator()
        start, end = orch.parse_period("2025-03")
        assert start == datetime(2025, 3, 1)
        assert end == datetime(2025, 4, 1)

    def test_2024_01_resolves(self):
        """Previously hardcoded year still works."""
        orch = _make_orchestrator()
        start, end = orch.parse_period("2024-01")
        assert start == datetime(2024, 1, 1)
        assert end == datetime(2024, 2, 1)

    def test_december_wraps_to_next_year(self):
        orch = _make_orchestrator()
        start, end = orch.parse_period("2025-12")
        assert start == datetime(2025, 12, 1)
        assert end == datetime(2026, 1, 1)

    def test_far_future_year(self):
        orch = _make_orchestrator()
        start, end = orch.parse_period("2099-06")
        assert start == datetime(2099, 6, 1)
        assert end == datetime(2099, 7, 1)


class TestParsePeriodQuarterly:
    def test_2025_Q1_resolves(self):
        orch = _make_orchestrator()
        start, end = orch.parse_period("2025-Q1")
        assert start == datetime(2025, 1, 1)
        assert end == datetime(2025, 4, 1)

    def test_2025_Q4_resolves(self):
        orch = _make_orchestrator()
        start, end = orch.parse_period("2025-Q4")
        assert start == datetime(2025, 10, 1)
        assert end == datetime(2026, 1, 1)


class TestParsePeriodBadInput:
    def test_missing_month_raises_validation_error(self):
        orch = _make_orchestrator()
        with pytest.raises(ApexValidationError, match="Unsupported period format"):
            orch.parse_period("2025")

    def test_invalid_month_raises_validation_error(self):
        orch = _make_orchestrator()
        with pytest.raises(ApexValidationError, match="Unsupported period format"):
            orch.parse_period("2025-13")

    def test_invalid_quarter_raises_validation_error(self):
        orch = _make_orchestrator()
        with pytest.raises(ApexValidationError, match="Unsupported period format"):
            orch.parse_period("2025-Q5")

    def test_garbage_input_raises_validation_error(self):
        orch = _make_orchestrator()
        with pytest.raises(ApexValidationError, match="Unsupported period format"):
            orch.parse_period("not-a-period")

    def test_empty_string_raises_validation_error(self):
        orch = _make_orchestrator()
        with pytest.raises(ApexValidationError, match="Unsupported period format"):
            orch.parse_period("")
