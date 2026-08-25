from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from redis import Redis

from app.core.exceptions import ApexTransientError
from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM
from app.models.sla import SLACalculationResult
from app.services.audit_log import audit_log
from app.services.metrics import _SLA_LATENCY_BUCKETS, increment_counter, record_histogram
from app.services.sla_cache import SLACache

logger = logging.getLogger(__name__)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """Return *dt* with UTC attached if it was timezone-naive."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


_sla_cache: SLACache | None = None


def _get_sla_cache() -> SLACache | None:
    """Lazily initialise the Redis-backed SLA cache."""
    global _sla_cache
    if _sla_cache is not None:
        return _sla_cache
    try:
        from app.core.config import settings as _settings

        _redis = Redis.from_url(_settings.REDIS_URL, decode_responses=True)
        _sla_cache = SLACache(_redis)
    except Exception:
        logger.debug("SLA cache unavailable; skipping cache lookups")
        return None
    return _sla_cache


class SLAOrchestrator:
    """Orchestrates SLA computation with real domain logic for outage-centric workflows."""

    def __init__(self, db: Session):
        self.db = db

    _MONTHLY_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
    _QUARTERLY_RE = re.compile(r"^(\d{4})-Q([1-4])$")

    def parse_period(self, period: str) -> tuple[datetime, datetime]:
        """Parse period string into start and end dates.

        Supported formats:
        - Monthly:  "YYYY-MM"  (e.g. "2025-03")
        - Quarterly: "YYYY-QN" (e.g. "2025-Q2")

        Raises:
            ApexValidationError: If the period string does not match either format.
        """
        from app.core.exceptions import ApexValidationError

        m = self._MONTHLY_RE.match(period)
        if m:
            year = int(m.group(1))
            month = int(m.group(2))
            start_date = datetime(year, month, 1, tzinfo=UTC)
            if month == 12:
                end_date = datetime(year + 1, 1, 1, tzinfo=UTC)
            else:
                end_date = datetime(year, month + 1, 1, tzinfo=UTC)
            return start_date, end_date

        m = self._QUARTERLY_RE.match(period)
        if m:
            year = int(m.group(1))
            quarter = int(m.group(2))
            start_month = (quarter - 1) * 3 + 1
            start_date = datetime(year, start_month, 1, tzinfo=UTC)
            if start_month == 10:
                end_date = datetime(year + 1, 1, 1, tzinfo=UTC)
            else:
                end_date = datetime(year, start_month + 3, 1, tzinfo=UTC)
            return start_date, end_date

        raise ApexValidationError(
            detail=f"Unsupported period format: '{period}'. Expected 'YYYY-MM' or 'YYYY-QN'.",
        )

    def get_outages_for_device(self, device_id: str, start_date: datetime, end_date: datetime) -> list[OutageORM]:
        """Get all outages for a device within the specified period."""
        return (
            self.db.query(OutageORM)
            .filter((OutageORM.site_id == device_id) | (OutageORM.id == device_id) | (OutageORM.site_name == device_id))
            .filter(OutageORM.created_at >= start_date)
            .filter(OutageORM.created_at < end_date)
            .all()
        )

    def calculate_mttr(self, outages: list[OutageORM]) -> float:
        """Calculate Mean Time To Resolution for outages."""
        if not outages:
            return 0.0

        mttr_values = []
        for outage in outages:
            started = _ensure_aware(outage.started_at)
            resolved = _ensure_aware(outage.resolved_at)
            if started and resolved:
                duration = resolved - started
                mttr_minutes = duration.total_seconds() / 60
                mttr_values.append(mttr_minutes)
            elif started:
                # For unresolved outages, calculate time since start
                duration = datetime.now(UTC) - started
                mttr_minutes = duration.total_seconds() / 60
                mttr_values.append(mttr_minutes)

        return round(sum(mttr_values) / len(mttr_values), 2) if mttr_values else 0.0

    def calculate_availability(self, outages: list[OutageORM], period_days: int) -> float:
        """Calculate availability percentage for the period."""
        if not outages:
            return 100.0

        total_minutes = period_days * 24 * 60
        downtime_minutes = 0

        for outage in outages:
            started = _ensure_aware(outage.started_at)
            resolved = _ensure_aware(outage.resolved_at)
            if started and resolved:
                downtime = resolved - started
                downtime_minutes += downtime.total_seconds() / 60
            elif started:
                # For unresolved outages, calculate downtime since start
                downtime = datetime.now(UTC) - started
                downtime_minutes += downtime.total_seconds() / 60

        availability = max(0.0, (total_minutes - downtime_minutes) / total_minutes * 100)
        return round(availability, 2)

    def check_sla_violations(self, availability: float, mttr: float, sla_thresholds: dict[str, float]) -> bool:
        """Check if SLA thresholds are violated."""
        availability_threshold = sla_thresholds.get("availability", 99.9)
        mttr_threshold = sla_thresholds.get("mttr", 60.0)  # minutes

        return availability < availability_threshold or mttr > mttr_threshold


def compute_device_sla(
    db: Session, device_id: str, period: str, sla_thresholds: dict[str, float] | None = None
) -> SLACalculationResult:
    """
    Compute SLA metrics for a device with real domain orchestration.

    This implementation provides outage-centric runtime behavior with:
    - Period parsing for monthly and quarterly periods
    - Real MTTR and availability calculations
    - SLA violation detection with configurable thresholds
    - Structured results aligned with routed API concepts

    Returns a SLACalculationResult Pydantic model rather than a loose dict
    so consumers get compile-time guarantees and OpenAPI can reflect the
    exact shape.  (#94)
    """
    import time

    start_time = time.monotonic()
    orchestrator = SLAOrchestrator(db)
    increment_counter("sla_recomputation_total", tags={"device_id": device_id, "period": period})

    # Check cache before computing
    cache = _get_sla_cache()
    if cache is not None:
        cached = cache.get(device_id, period)
        if cached is not None:
            return SLACalculationResult.model_validate(cached)

    # Default SLA thresholds if not provided
    if sla_thresholds is None:
        sla_thresholds = {
            "availability": 99.9,  # 99.9% availability
            "mttr": 60.0,  # 60 minutes MTTR
        }

    try:
        start_date, end_date = orchestrator.parse_period(period)
        period_days = (end_date - start_date).days

        # Get outages for the device and period
        outages = orchestrator.get_outages_for_device(device_id, start_date, end_date)

        if not outages:
            result = SLACalculationResult(
                device_id=device_id,
                period=period,
                period_start=start_date.isoformat(),
                period_end=end_date.isoformat(),
                total_outages=0,
                violated_outages=0,
                avg_mttr_minutes=0.0,
                availability_percentage=100.0,
                is_violated=False,
                sla_thresholds=sla_thresholds,
                violation_reasons=[],
            )
            latency = time.monotonic() - start_time
            record_histogram("sla_computation_latency_seconds", latency, tags={"period": period, "status": "no_outages"}, buckets=_SLA_LATENCY_BUCKETS)
            record_sla_settlement_audit_events(device_id, period, result, status="initiated")
            record_sla_settlement_audit_events(device_id, period, result, status="succeeded")
            if cache is not None:
                cache.set(device_id, period, result.model_dump())
            return result

        # Calculate metrics
        mttr = orchestrator.calculate_mttr(outages)
        availability = orchestrator.calculate_availability(outages, period_days)
        is_violated = orchestrator.check_sla_violations(availability, mttr, sla_thresholds)

        # Determine violation reasons
        if is_violated:
            increment_counter("sla_violation_total", tags={"device_id": device_id, "period": period})
        violation_reasons = []
        if availability < sla_thresholds["availability"]:
            violation_reasons.append(f"Availability {availability}% below threshold {sla_thresholds['availability']}%")
        if mttr > sla_thresholds["mttr"]:
            violation_reasons.append(f"MTTR {mttr} minutes above threshold {sla_thresholds['mttr']} minutes")

        # Get latest SLA results for additional context
        outage_ids = [outage.id for outage in outages]
        latest_results = {}
        if outage_ids:
            rows = (
                db.query(SLAResultORM)
                .filter(SLAResultORM.outage_id.in_(outage_ids))
                .order_by(SLAResultORM.outage_id, SLAResultORM.created_at.desc(), SLAResultORM.id.desc())
                .all()
            )
            for row in rows:
                latest_results.setdefault(row.outage_id, row)

        violated_outages = sum(1 for r in latest_results.values() if r and r.status == "violated")

        result = SLACalculationResult(
            device_id=device_id,
            period=period,
            period_start=start_date.isoformat(),
            period_end=end_date.isoformat(),
            total_outages=len(outages),
            violated_outages=violated_outages,
            avg_mttr_minutes=mttr,
            availability_percentage=availability,
            is_violated=is_violated,
            sla_thresholds=sla_thresholds,
            violation_reasons=violation_reasons,
            outage_details=[
                {
                    "id": outage.id,
                    "site_id": outage.site_id,
                    "site_name": outage.site_name,
                    "started_at": outage.started_at.isoformat() if outage.started_at else None,
                    "resolved_at": outage.resolved_at.isoformat() if outage.resolved_at else None,
                    "severity": getattr(outage, "severity", "unknown"),
                }
                for outage in outages
            ],
        )
        latency = time.monotonic() - start_time
        record_histogram("sla_computation_latency_seconds", latency, tags={"period": period, "status": "violated" if is_violated else "ok"}, buckets=_SLA_LATENCY_BUCKETS)
        record_sla_settlement_audit_events(device_id, period, result, status="initiated")
        record_sla_settlement_audit_events(device_id, period, result, status="succeeded")
        if cache is not None:
            cache.set(device_id, period, result.model_dump())
        return result

    except ApexTransientError:
        raise  # already typed, let it propagate
    except Exception as e:
        logger.exception("SLA computation failed for device %s", device_id)
        audit_log.log(
            event_type="sla_settlement_failed",
            details={"device_id": device_id, "period": period, "error": str(e)},
        )
        raise ApexTransientError(detail=f"SLA computation failed for device {device_id}: {e}") from e


def record_sla_settlement_audit_events(
    device_id: str,
    period: str,
    sla_result: SLACalculationResult | dict[str, Any],
    *,
    status: str = "initiated",
    error: str | None = None,
) -> None:
    event_type = f"sla_settlement_{status}"
    details = {
        "device_id": device_id,
        "period": period,
        "sla_result": sla_result,
    }
    if error:
        details["error"] = error
    audit_log.log(event_type=event_type, details=details)
