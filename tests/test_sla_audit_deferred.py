"""Tests for deferred SLA audit events — Issue #236.

Verifies that compute_device_sla() does NOT emit audit events directly.
Audit logging must happen in callers AFTER the transaction commits,
so the computation function itself should have no audit side effects.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.models.sla import SLACalculationResult
from app.services.sla_service import (
    compute_device_sla,
    record_sla_settlement_audit_events,
)


def _make_result(device_id="dev-1", period="2025-03", **overrides):
    defaults = dict(
        device_id=device_id,
        period=period,
        period_start="2025-03-01T00:00:00",
        period_end="2025-04-01T00:00:00",
        total_outages=0,
        violated_outages=0,
        avg_mttr_minutes=0.0,
        availability_percentage=100.0,
        is_violated=False,
        sla_thresholds={"availability": 99.9, "mttr": 60.0},
        violation_reasons=[],
    )
    defaults.update(overrides)
    return SLACalculationResult(**defaults)


class TestComputeDeviceSlaNoAuditEvents:
    """compute_device_sla() must not call record_sla_settlement_audit_events."""

    @patch("app.services.sla_service.audit_log")
    @patch("app.services.sla_service.SLAOrchestrator")
    def test_no_outages_does_not_emit_audit(self, MockOrchestrator, mock_audit):
        orch = MagicMock()
        MockOrchestrator.return_value = orch
        orch.parse_period.return_value = (
            datetime(2025, 3, 1),
            datetime(2025, 4, 1),
        )
        orch.get_outages_for_device.return_value = []
        db = MagicMock()

        result = compute_device_sla(db, "dev-1", "2025-03")

        assert isinstance(result, SLACalculationResult)
        mock_audit.log.assert_not_called()

    @patch("app.services.sla_service.audit_log")
    @patch("app.services.sla_service.SLAOrchestrator")
    def test_with_outages_does_not_emit_audit(self, MockOrchestrator, mock_audit):
        orch = MagicMock()
        MockOrchestrator.return_value = orch
        orch.parse_period.return_value = (
            datetime(2025, 3, 1),
            datetime(2025, 4, 1),
        )
        outage = MagicMock()
        outage.id = "o-1"
        outage.site_id = "s-1"
        outage.site_name = "Site A"
        outage.started_at = datetime(2025, 3, 5, 10, 0)
        outage.resolved_at = datetime(2025, 3, 5, 12, 0)
        outage.severity = "high"
        orch.get_outages_for_device.return_value = [outage]
        orch.calculate_mttr.return_value = 120.0
        orch.calculate_availability.return_value = 99.5
        orch.check_sla_violations.return_value = False

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = compute_device_sla(db, "dev-1", "2025-03")

        assert isinstance(result, SLACalculationResult)
        mock_audit.log.assert_not_called()

    @patch("app.services.sla_service.audit_log")
    @patch("app.services.sla_service.SLAOrchestrator")
    def test_failure_path_emits_audit(self, MockOrchestrator, mock_audit):
        """The exception handler in compute_device_sla should still log failures."""
        orch = MagicMock()
        MockOrchestrator.return_value = orch
        orch.parse_period.side_effect = ValueError("bad period")
        db = MagicMock()

        try:
            compute_device_sla(db, "dev-1", "bad-period")
        except Exception:
            pass

        mock_audit.log.assert_called_once()
        call_kwargs = mock_audit.log.call_args
        assert call_kwargs[1]["event_type"] == "sla_settlement_failed"


class TestRecordSlaSettlementAuditEvents:
    """record_sla_settlement_audit_events is a plain helper — smoke test it works."""

    @patch("app.services.sla_service.audit_log")
    def test_emits_initiated(self, mock_audit):
        result = _make_result()
        record_sla_settlement_audit_events("dev-1", "2025-03", result, status="initiated")

        mock_audit.log.assert_called_once()
        args, kwargs = mock_audit.log.call_args
        assert kwargs["event_type"] == "sla_settlement_initiated"

    @patch("app.services.sla_service.audit_log")
    def test_emits_succeeded(self, mock_audit):
        result = _make_result()
        record_sla_settlement_audit_events("dev-1", "2025-03", result, status="succeeded")

        mock_audit.log.assert_called_once()
        args, kwargs = mock_audit.log.call_args
        assert kwargs["event_type"] == "sla_settlement_succeeded"

    @patch("app.services.sla_service.audit_log")
    def test_emits_with_error(self, mock_audit):
        result = _make_result()
        record_sla_settlement_audit_events(
            "dev-1", "2025-03", result, status="failed", error="db timeout"
        )

        mock_audit.log.assert_called_once()
        args, kwargs = mock_audit.log.call_args
        assert kwargs["event_type"] == "sla_settlement_failed"
        assert "error" in kwargs["details"]
        assert kwargs["details"]["error"] == "db timeout"
