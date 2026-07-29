from datetime import datetime, timezone
from typing import List, Literal, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM
from app.models.orm.sla_snapshot import SLAAnalyticsSnapshotORM
from app.models.sla import SLAResult, SLADashboardKPI, SLAPerformanceAggregation, SLATrendPoint, SLAAnalyticsSnapshot

BucketInterval = Literal["day", "week", "month"]
VALID_BUCKETS: tuple[str, ...] = ("day", "week", "month")


def _orm_to_pydantic(orm: SLAResultORM) -> SLAResult:
    return SLAResult(
        id=orm.id,
        outage_id=orm.outage_id,
        status=orm.status,
        mttr_minutes=orm.mttr_minutes,
        threshold_minutes=orm.threshold_minutes,
        amount=orm.amount,
        payment_type=orm.payment_type,
        rating=orm.rating,
        policy_version=orm.policy_version,
        threshold_source=orm.threshold_source,
        reason_code=orm.reason_code,
        decision_trace=orm.decision_trace,
        compute_hash=orm.compute_hash,
    )


class SLARepository:
    def __init__(self, db: Session):
        self.db = db

    def find_by_compute_hash(self, outage_id: str, compute_hash: str) -> Optional[SLAResultORM]:
        """Look up an existing SLA result by outage_id and compute_hash (#35)."""
        return (
            self.db.query(SLAResultORM)
            .filter(
                SLAResultORM.outage_id == outage_id,
                SLAResultORM.compute_hash == compute_hash,
            )
            .first()
        )

    def create(self, sla_data: SLAResult | Mapping[str, object]) -> SLAResult:
        if isinstance(sla_data, SLAResult):
            payload = sla_data.model_dump()
        else:
            payload = dict(sla_data)

        compute_hash_val = payload.get("compute_hash")

        # Idempotency check: if compute_hash is present, check for existing row (#35)
        if compute_hash_val:
            existing = self.find_by_compute_hash(payload["outage_id"], compute_hash_val)
            if existing:
                self.db.refresh(existing)
                return _orm_to_pydantic(existing)

        # Use row-level locking to prevent race conditions when updating latest flag
        # First, lock any existing latest row for this outage
        existing_latest = (
            self.db.query(SLAResultORM)
            .filter(SLAResultORM.outage_id == payload["outage_id"], SLAResultORM.is_latest.is_(True))
            .with_for_update(nowait=False)
            .first()
        )

        # Demote any existing latest record for this outage (#154, #219)
        if existing_latest:
            self.db.execute(
                update(SLAResultORM)
                .where(SLAResultORM.outage_id == payload["outage_id"])
                .where(SLAResultORM.is_latest.is_(True))
                .values(is_latest=False)
            )

        orm = SLAResultORM(
            outage_id=payload["outage_id"],
            status=payload["status"],
            mttr_minutes=payload["mttr_minutes"],
            threshold_minutes=payload["threshold_minutes"],
            amount=payload["amount"],
            payment_type=payload["payment_type"],
            rating=payload["rating"],
            policy_version=payload.get("policy_version", "1.0"),
            threshold_source=payload.get("threshold_source", "config"),
            is_latest=True,
            reason_code=payload.get("reason_code"),
            decision_trace=payload.get("decision_trace"),
            compute_hash=compute_hash_val,
        )
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return _orm_to_pydantic(orm)

    def create_if_changed(self, sla_data: SLAResult | Mapping[str, object]) -> SLAResult:
        if isinstance(sla_data, SLAResult):
            payload = sla_data.model_dump()
        else:
            payload = dict(sla_data)

        # Idempotency via compute_hash first (#35)
        compute_hash_val = payload.get("compute_hash")
        if compute_hash_val:
            existing = self.find_by_compute_hash(payload["outage_id"], compute_hash_val)
            if existing:
                self.db.refresh(existing)
                return _orm_to_pydantic(existing)

        latest = self.get_by_outage(payload["outage_id"])
        if latest and latest.model_dump() == payload:
            return latest

        return self.create(payload)

    def get_by_outage(self, outage_id: str) -> Optional[SLAResult]:
        """Return the authoritative latest SLA result for an outage (#154)."""
        orm = (
            self.db.query(SLAResultORM)
            .filter(SLAResultORM.outage_id == outage_id, SLAResultORM.is_latest.is_(True))
            .first()
        )
        if not orm:
            # Fallback: no is_latest row yet (pre-migration data)
            orm = (
                self.db.query(SLAResultORM)
                .filter(SLAResultORM.outage_id == outage_id)
                .order_by(SLAResultORM.created_at.desc())
                .first()
            )
        if not orm:
            return None
        return _orm_to_pydantic(orm)

    def list_by_outage(self, outage_id: str) -> List[SLAResult]:
        rows = (
            self.db.query(SLAResultORM)
            .filter(SLAResultORM.outage_id == outage_id)
            .order_by(SLAResultORM.created_at.desc())
            .all()
        )
        return [_orm_to_pydantic(r) for r in rows]

    def aggregate_performance(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        severity: Optional[str] = None,
        site_id: Optional[str] = None,
    ) -> SLAPerformanceAggregation:
        latest_results_query = select(
            SLAResultORM.outage_id.label("outage_id"),
            SLAResultORM.status.label("status"),
            SLAResultORM.mttr_minutes.label("mttr_minutes"),
            SLAResultORM.amount.label("amount"),
            func.row_number()
            .over(
                partition_by=SLAResultORM.outage_id,
                order_by=(SLAResultORM.created_at.desc(), SLAResultORM.id.desc()),
            )
            .label("rn"),
        ).join(OutageORM, OutageORM.id == SLAResultORM.outage_id)

        if start_date:
            latest_results_query = latest_results_query.where(SLAResultORM.created_at >= start_date)
        if end_date:
            latest_results_query = latest_results_query.where(SLAResultORM.created_at <= end_date)
        if severity:
            latest_results_query = latest_results_query.where(OutageORM.severity == severity)
        if site_id:
            latest_results_query = latest_results_query.where(OutageORM.site_id == site_id)

        latest_results = latest_results_query.subquery()

        aggregate_query = select(
            func.count(latest_results.c.outage_id).label("total_outages"),
            func.coalesce(
                func.sum(case((latest_results.c.status == "violated", 1), else_=0)),
                0,
            ).label("total_violations"),
            func.coalesce(func.avg(latest_results.c.mttr_minutes), 0.0).label("avg_mttr"),
            func.coalesce(func.sum(latest_results.c.amount), 0.0).label("payout_sum"),
        ).where(latest_results.c.rn == 1)

        row = self.db.execute(aggregate_query).one()
        total_outages = int(row.total_outages or 0)
        total_violations = int(row.total_violations or 0)
        violation_rate = 0.0 if total_outages == 0 else total_violations / total_outages

        return SLAPerformanceAggregation(
            total_outages=total_outages,
            violation_rate=round(float(violation_rate), 4),
            avg_mttr=round(float(row.avg_mttr or 0.0), 2),
            payout_sum=round(float(row.payout_sum or 0.0), 2),
        )

    def aggregate_dashboard_kpis(
        self,
        severity: Optional[str] = None,
        site_id: Optional[str] = None,
    ) -> SLADashboardKPI:
        query = select(
            func.count(SLAResultORM.id).label("total_outages"),
            func.coalesce(
                func.sum(case((SLAResultORM.status == "violated", 1), else_=0)),
                0,
            ).label("total_violations"),
            func.coalesce(
                func.sum(case((SLAResultORM.payment_type == "reward", SLAResultORM.amount), else_=0.0)),
                0.0,
            ).label("total_rewards"),
            func.coalesce(
                func.sum(case((SLAResultORM.payment_type == "penalty", func.abs(SLAResultORM.amount)), else_=0.0)),
                0.0,
            ).label("total_penalties"),
        )

        if severity or site_id:
            query = query.join(OutageORM, OutageORM.id == SLAResultORM.outage_id)
            if severity:
                query = query.where(OutageORM.severity == severity)
            if site_id:
                query = query.where(OutageORM.site_id == site_id)

        row = self.db.execute(query).one()
        total_rewards = round(float(row.total_rewards or 0.0), 2)
        total_penalties = round(float(row.total_penalties or 0.0), 2)
        return SLADashboardKPI(
            total_outages=int(row.total_outages or 0),
            total_violations=int(row.total_violations or 0),
            total_rewards=total_rewards,
            total_penalties=total_penalties,
            net_payout=round(total_rewards - total_penalties, 2),
        )

    def aggregate_trends(
        self,
        limit_days: int = 7,
        bucket: BucketInterval = "day",
        tz: str = "UTC",
        severity: Optional[str] = None,
        site_id: Optional[str] = None,
    ) -> List[SLATrendPoint]:
        if bucket not in VALID_BUCKETS:
            raise ValueError(f"Invalid bucket '{bucket}'. Must be one of: {', '.join(VALID_BUCKETS)}")

        try:
            _ = ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            raise ValueError(f"Unknown timezone: '{tz}'")

        # Truncate created_at to the requested bucket in the target timezone.
        # We convert UTC → target tz using AT TIME ZONE (PostgreSQL).
        if bucket == "day":
            bucket_expr = func.date_trunc("day", func.timezone(tz, SLAResultORM.created_at))
        elif bucket == "week":
            bucket_expr = func.date_trunc("week", func.timezone(tz, SLAResultORM.created_at))
        else:  # month
            bucket_expr = func.date_trunc("month", func.timezone(tz, SLAResultORM.created_at))

        query = (
            select(
                bucket_expr.label("bucket"),
                func.count(SLAResultORM.id).label("total_outages"),
                func.coalesce(
                    func.sum(case((SLAResultORM.status == "violated", 1), else_=0)),
                    0,
                ).label("violations"),
                func.coalesce(
                    func.sum(case((SLAResultORM.payment_type == "reward", SLAResultORM.amount), else_=0.0)),
                    0.0,
                ).label("rewards"),
                func.coalesce(
                    func.sum(case((SLAResultORM.payment_type == "penalty", func.abs(SLAResultORM.amount)), else_=0.0)),
                    0.0,
                ).label("penalties"),
            )
            .group_by(bucket_expr)
            .order_by(bucket_expr.desc())
            .limit(limit_days)
        )

        if severity or site_id:
            query = query.join(OutageORM, OutageORM.id == SLAResultORM.outage_id)
            if severity:
                query = query.where(OutageORM.severity == severity)
            if site_id:
                query = query.where(OutageORM.site_id == site_id)

        rows = self.db.execute(query).all()

        return [
            SLATrendPoint(
                date=str(row.bucket),
                total_outages=int(row.total_outages or 0),
                violations=int(row.violations or 0),
                rewards=round(float(row.rewards or 0.0), 2),
                penalties=round(float(row.penalties or 0.0), 2),
            )
            for row in rows
        ][::-1]

    def create_snapshot(self, snapshot_key: str = "global") -> SLAAnalyticsSnapshot:
        """Materialize current dashboard KPIs into a snapshot row."""
        kpis = self.aggregate_dashboard_kpis()
        perf = self.aggregate_performance()
        orm = SLAAnalyticsSnapshotORM(
            snapshot_key=snapshot_key,
            total_outages=kpis.total_outages,
            total_violations=kpis.total_violations,
            total_rewards=kpis.total_rewards,
            total_penalties=kpis.total_penalties,
            net_payout=kpis.net_payout,
            avg_mttr=perf.avg_mttr,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            checksum="",  # Temporary value, will be computed
        )
        orm.checksum = orm.compute_checksum()
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return SLAAnalyticsSnapshot(
            id=orm.id,
            snapshot_key=orm.snapshot_key,
            total_outages=orm.total_outages,
            total_violations=orm.total_violations,
            total_rewards=orm.total_rewards,
            total_penalties=orm.total_penalties,
            net_payout=orm.net_payout,
            avg_mttr=orm.avg_mttr,
            checksum=orm.checksum,
            created_at=str(orm.created_at),
        )

    def get_latest_snapshot(self, snapshot_key: str = "global") -> Optional[SLAAnalyticsSnapshot]:
        """Return the most recent snapshot for the given key."""
        orm = (
            self.db.query(SLAAnalyticsSnapshotORM)
            .filter(SLAAnalyticsSnapshotORM.snapshot_key == snapshot_key)
            .order_by(SLAAnalyticsSnapshotORM.created_at.desc())
            .first()
        )
        if not orm:
            return None
        return SLAAnalyticsSnapshot(
            id=orm.id,
            snapshot_key=orm.snapshot_key,
            total_outages=orm.total_outages,
            total_violations=orm.total_violations,
            total_rewards=orm.total_rewards,
            total_penalties=orm.total_penalties,
            net_payout=orm.net_payout,
            avg_mttr=orm.avg_mttr,
            checksum=orm.checksum,
            created_at=str(orm.created_at),
        )

    def rebuild_snapshot(self, snapshot_key: str = "global") -> SLAAnalyticsSnapshot:
        """Rebuild a snapshot from current live data. Idempotent operation.

        This method:
        1. Aggregates current SLA data from scratch
        2. Creates a new snapshot row (doesn't delete old ones)
        3. Returns the new snapshot

        Safe for reconciliation after migrations or data drift.
        """
        # Aggregate fresh data
        kpis = self.aggregate_dashboard_kpis()
        perf = self.aggregate_performance()

        # Create new snapshot with current data
        orm = SLAAnalyticsSnapshotORM(
            snapshot_key=snapshot_key,
            total_outages=kpis.total_outages,
            total_violations=kpis.total_violations,
            total_rewards=kpis.total_rewards,
            total_penalties=kpis.total_penalties,
            net_payout=kpis.net_payout,
            avg_mttr=perf.avg_mttr,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            checksum="",
        )
        orm.checksum = orm.compute_checksum()
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)

        return SLAAnalyticsSnapshot(
            id=orm.id,
            snapshot_key=orm.snapshot_key,
            total_outages=orm.total_outages,
            total_violations=orm.total_violations,
            total_rewards=orm.total_rewards,
            total_penalties=orm.total_penalties,
            net_payout=orm.net_payout,
            avg_mttr=orm.avg_mttr,
            checksum=orm.checksum,
            created_at=str(orm.created_at),
        )

    def verify_snapshot_integrity(self, snapshot_key: str = "global") -> dict:
        """Verify integrity of the latest snapshot.

        Returns a dict with:
        - "valid": bool indicating if the snapshot is intact
        - "snapshot_id": int if snapshot exists
        - "error": str if invalid or snapshot missing
        """
        orm = (
            self.db.query(SLAAnalyticsSnapshotORM)
            .filter(SLAAnalyticsSnapshotORM.snapshot_key == snapshot_key)
            .order_by(SLAAnalyticsSnapshotORM.created_at.desc())
            .first()
        )
        if not orm:
            return {"valid": False, "error": "No snapshot found"}
        computed_checksum = orm.compute_checksum()
        is_valid = computed_checksum == orm.checksum
        result = {"valid": is_valid, "snapshot_id": orm.id}
        if not is_valid:
            result["error"] = "Checksum mismatch - snapshot may have been tampered with"
        return result

    def reconcile_snapshots(self, snapshot_key: str = "global") -> dict:
        """Reconcile snapshots by comparing latest snapshot with live data.

        Returns reconciliation report showing:
        - Whether the latest snapshot matches current live aggregates
        - Differences if any exist
        - Recommendation to rebuild if drifted

        This is a read-only operation that helps identify data drift.
        """
        # Get latest snapshot
        latest_snapshot = self.get_latest_snapshot(snapshot_key)

        # Calculate current live aggregates
        current_kpis = self.aggregate_dashboard_kpis()
        current_perf = self.aggregate_performance()

        if not latest_snapshot:
            return {
                "snapshot_key": snapshot_key,
                "has_snapshot": False,
                "recommendation": "rebuild",
                "message": "No snapshot exists. Rebuild recommended.",
                "current_live_data": {
                    "total_outages": current_kpis.total_outages,
                    "total_violations": current_kpis.total_violations,
                    "total_rewards": current_kpis.total_rewards,
                    "total_penalties": current_kpis.total_penalties,
                    "net_payout": current_kpis.net_payout,
                    "avg_mttr": current_perf.avg_mttr,
                },
            }

        # Compare snapshot with live data
        drift_detected = (
            latest_snapshot.total_outages != current_kpis.total_outages
            or latest_snapshot.total_violations != current_kpis.total_violations
            or latest_snapshot.total_rewards != current_kpis.total_rewards
            or latest_snapshot.total_penalties != current_kpis.total_penalties
            or abs(latest_snapshot.net_payout - current_kpis.net_payout) > 0.01
            or abs(latest_snapshot.avg_mttr - current_perf.avg_mttr) > 0.01
        )

        differences = {}
        if drift_detected:
            if latest_snapshot.total_outages != current_kpis.total_outages:
                differences["total_outages"] = {
                    "snapshot": latest_snapshot.total_outages,
                    "live": current_kpis.total_outages,
                    "diff": current_kpis.total_outages - latest_snapshot.total_outages,
                }
            if latest_snapshot.total_violations != current_kpis.total_violations:
                differences["total_violations"] = {
                    "snapshot": latest_snapshot.total_violations,
                    "live": current_kpis.total_violations,
                    "diff": current_kpis.total_violations - latest_snapshot.total_violations,
                }
            if latest_snapshot.total_rewards != current_kpis.total_rewards:
                differences["total_rewards"] = {
                    "snapshot": latest_snapshot.total_rewards,
                    "live": current_kpis.total_rewards,
                    "diff": round(current_kpis.total_rewards - latest_snapshot.total_rewards, 2),
                }
            if latest_snapshot.total_penalties != current_kpis.total_penalties:
                differences["total_penalties"] = {
                    "snapshot": latest_snapshot.total_penalties,
                    "live": current_kpis.total_penalties,
                    "diff": round(current_kpis.total_penalties - latest_snapshot.total_penalties, 2),
                }
            if abs(latest_snapshot.net_payout - current_kpis.net_payout) > 0.01:
                differences["net_payout"] = {
                    "snapshot": latest_snapshot.net_payout,
                    "live": current_kpis.net_payout,
                    "diff": round(current_kpis.net_payout - latest_snapshot.net_payout, 2),
                }
            if abs(latest_snapshot.avg_mttr - current_perf.avg_mttr) > 0.01:
                differences["avg_mttr"] = {
                    "snapshot": latest_snapshot.avg_mttr,
                    "live": current_perf.avg_mttr,
                    "diff": round(current_perf.avg_mttr - latest_snapshot.avg_mttr, 2),
                }

        return {
            "snapshot_key": snapshot_key,
            "has_snapshot": True,
            "snapshot_id": latest_snapshot.id,
            "snapshot_created_at": latest_snapshot.created_at,
            "drift_detected": drift_detected,
            "recommendation": "rebuild" if drift_detected else "ok",
            "differences": differences if drift_detected else None,
            "current_live_data": {
                "total_outages": current_kpis.total_outages,
                "total_violations": current_kpis.total_violations,
                "total_rewards": current_kpis.total_rewards,
                "total_penalties": current_kpis.total_penalties,
                "net_payout": current_kpis.net_payout,
                "avg_mttr": current_perf.avg_mttr,
            },
            "snapshot_data": {
                "total_outages": latest_snapshot.total_outages,
                "total_violations": latest_snapshot.total_violations,
                "total_rewards": latest_snapshot.total_rewards,
                "total_penalties": latest_snapshot.total_penalties,
                "net_payout": latest_snapshot.net_payout,
                "avg_mttr": latest_snapshot.avg_mttr,
            },
        }
