from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM
from app.services.metrics import increment_counter


class SLAOrchestrator:
    """Orchestrates SLA computation with real domain logic for outage-centric workflows."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def parse_period(self, period: str) -> tuple[datetime, datetime]:
        """Parse period string into start and end dates."""
        if period.startswith("2024-") and len(period) == 7:  # Monthly format "2024-01"
            year = int(period.split("-")[0])
            month = int(period.split("-")[1])
            start_date = datetime(year, month, 1)
            if month == 12:
                end_date = datetime(year + 1, 1, 1)
            else:
                end_date = datetime(year, month + 1, 1)
            return start_date, end_date
        elif "Q" in period:  # Quarterly format "2024-Q1"
            year = int(period.split("-")[0])
            quarter = int(period.split("Q")[1])
            start_month = (quarter - 1) * 3 + 1
            start_date = datetime(year, start_month, 1)
            if start_month == 10:
                end_date = datetime(year + 1, 1, 1)
            else:
                end_date = datetime(year, start_month + 3, 1)
            return start_date, end_date
        else:
            raise ValueError(f"Unsupported period format: {period}")
    
    def get_outages_for_device(self, device_id: str, start_date: datetime, end_date: datetime) -> List[OutageORM]:
        """Get all outages for a device within the specified period."""
        return (
            self.db.query(OutageORM)
            .filter(
                (OutageORM.site_id == device_id)
                | (OutageORM.id == device_id)
                | (OutageORM.site_name == device_id)
            )
            .filter(OutageORM.created_at >= start_date)
            .filter(OutageORM.created_at < end_date)
            .all()
        )
    
    def calculate_mttr(self, outages: List[OutageORM]) -> float:
        """Calculate Mean Time To Resolution for outages."""
        if not outages:
            return 0.0
        
        mttr_values = []
        for outage in outages:
            if outage.started_at and outage.resolved_at:
                duration = outage.resolved_at - outage.started_at
                mttr_minutes = duration.total_seconds() / 60
                mttr_values.append(mttr_minutes)
            elif outage.started_at:
                # For unresolved outages, calculate time since start
                duration = datetime.now(timezone.utc) - outage.started_at
                mttr_minutes = duration.total_seconds() / 60
                mttr_values.append(mttr_minutes)
        
        return round(sum(mttr_values) / len(mttr_values), 2) if mttr_values else 0.0
    
    def calculate_availability(self, outages: List[OutageORM], period_days: int) -> float:
        """Calculate availability percentage for the period."""
        if not outages:
            return 100.0
        
        total_minutes = period_days * 24 * 60
        downtime_minutes = 0
        
        for outage in outages:
            if outage.started_at and outage.resolved_at:
                downtime = outage.resolved_at - outage.started_at
                downtime_minutes += downtime.total_seconds() / 60
            elif outage.started_at:
                # For unresolved outages, calculate downtime since start
                downtime = datetime.now(timezone.utc) - outage.started_at
                downtime_minutes += downtime.total_seconds() / 60
        
        availability = max(0.0, (total_minutes - downtime_minutes) / total_minutes * 100)
        return round(availability, 2)
    
    def check_sla_violations(self, availability: float, mttr: float, sla_thresholds: Dict[str, float]) -> bool:
        """Check if SLA thresholds are violated."""
        availability_threshold = sla_thresholds.get("availability", 99.9)
        mttr_threshold = sla_thresholds.get("mttr", 60.0)  # minutes
        
        return availability < availability_threshold or mttr > mttr_threshold


def compute_device_sla(db: Session, device_id: str, period: str, sla_thresholds: Optional[Dict[str, float]] = None) -> dict:
    """
    Compute SLA metrics for a device with real domain orchestration.
    
    This implementation provides outage-centric runtime behavior with:
    - Period parsing for monthly and quarterly periods
    - Real MTTR and availability calculations
    - SLA violation detection with configurable thresholds
    - Structured results aligned with routed API concepts
    """
    orchestrator = SLAOrchestrator(db)
    increment_counter("sla_recomputation_total", tags={"device_id": device_id, "period": period})
    
    # Default SLA thresholds if not provided
    if sla_thresholds is None:
        sla_thresholds = {
            "availability": 99.9,  # 99.9% availability
            "mttr": 60.0           # 60 minutes MTTR
        }
    
    try:
        start_date, end_date = orchestrator.parse_period(period)
        period_days = (end_date - start_date).days
        
        # Get outages for the device and period
        outages = orchestrator.get_outages_for_device(device_id, start_date, end_date)
        
        if not outages:
            return {
                "device_id": device_id,
                "period": period,
                "period_start": start_date.isoformat(),
                "period_end": end_date.isoformat(),
                "total_outages": 0,
                "violated_outages": 0,
                "avg_mttr_minutes": 0.0,
                "availability_percentage": 100.0,
                "is_violated": False,
                "sla_thresholds": sla_thresholds,
                "violation_reasons": []
            }
        
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
        
        violated_outages = sum(1 for result in latest_results.values() if result and result.status == "violated")
        
        return {
            "device_id": device_id,
            "period": period,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "total_outages": len(outages),
            "violated_outages": violated_outages,
            "avg_mttr_minutes": mttr,
            "availability_percentage": availability,
            "is_violated": is_violated,
            "sla_thresholds": sla_thresholds,
            "violation_reasons": violation_reasons,
            "outage_details": [
                {
                    "id": outage.id,
                    "site_id": outage.site_id,
                    "site_name": outage.site_name,
                    "started_at": outage.started_at.isoformat() if outage.started_at else None,
                    "resolved_at": outage.resolved_at.isoformat() if outage.resolved_at else None,
                    "severity": getattr(outage, 'severity', 'unknown')
                }
                for outage in outages
            ]
        }
        
    except Exception as e:
        # Return error structure that aligns with API expectations
        return {
            "device_id": device_id,
            "period": period,
            "error": str(e),
            "is_violated": False,
            "error_type": "computation_failed"
        }
