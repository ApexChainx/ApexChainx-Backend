"""Regression coverage for SLA calculations against the current outage ORM schema."""

from datetime import UTC, datetime

from app.models.orm.outage import OutageORM
from app.services.sla_service import SLAOrchestrator


def _outage(*, resolved: datetime | None) -> OutageORM:
    return OutageORM(
        id="outage-377",
        site_name="Site 377",
        site_id="device-377",
        severity="high",
        status="resolved" if resolved else "open",
        detected_at=datetime(2025, 3, 5, 10, 0, tzinfo=UTC),
        resolved_at=resolved,
        description="Regression outage",
        affected_services=["internet"],
        created_at=datetime(2025, 3, 5, 10, 0, tzinfo=UTC),
        updated_at=datetime(2025, 3, 5, 10, 0, tzinfo=UTC),
    )


def test_sla_metrics_use_detected_at_on_real_outage_orm() -> None:
    orchestrator = SLAOrchestrator(db=None)  # type: ignore[arg-type]
    outage = _outage(resolved=datetime(2025, 3, 5, 12, 0, tzinfo=UTC))

    assert orchestrator.calculate_mttr([outage]) == 120.0
    assert orchestrator.calculate_availability([outage], period_days=31) == 99.73


def test_real_outage_orm_has_no_stale_started_at_attribute() -> None:
    outage = _outage(resolved=datetime(2025, 3, 5, 12, 0, tzinfo=UTC))

    assert not hasattr(outage, "started_at")
    assert outage.detected_at is not None
