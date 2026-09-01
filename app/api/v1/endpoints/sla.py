from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.security import require_admin, require_engineer
from app.db.session import get_db
from app.models import SLAResult
from app.models.sla import (
    SLAAnalyticsSnapshot,
    SLAConfigUpdateRequest,
    SLADashboardKPI,
    SLAPerformanceAggregation,
    SLAPreviewRequest,
    SLATrendPoint,
)
from app.repositories.sla_repository import VALID_BUCKETS, SLARepository
from app.services.sla import SLACalculator
from app.services.sla.config import (
    ConcurrencyError,
    get_all_config,
    get_all_config_with_hashes,
    get_config_for_severity,
    get_config_with_hash,
    get_current_token,
    publish_config_for_severity,
    update_config_for_severity,
)
from app.utils.analytics_exporter import (
    export_analytics_summary,
    export_dashboard_kpi,
    export_performance_aggregation,
    export_trends,
)
from app.utils.cache import TTLCache

router = APIRouter()

# Cache dashboard KPIs and trends for 30 seconds to reduce repeated DB load.
_dashboard_cache: TTLCache = TTLCache(ttl_seconds=30)


def _invalidate_analytics_cache() -> None:
    """Invalidate all analytics cache keys after a mutating write (#157)."""
    _dashboard_cache.invalidate_prefix("dashboard_kpis_")
    # Invalidate all trend keys by clearing the entire store
    _dashboard_cache.invalidate_prefix("trends_")


@router.get("/calculate", response_model=SLAResult)
def calculate_sla(
    outage_id: str,
    severity: str,
    mttr_minutes: int,
    policy_version: str = "1.0",
    threshold_source: str = "config",
    current_user=Depends(require_engineer),
):
    """Calculate SLA result for given outage metrics (BE-009)."""
    try:
        return SLACalculator.calculate(
            outage_id=outage_id,
            severity=severity,
            mttr_minutes=mttr_minutes,
            policy_version=policy_version,
            threshold_source=threshold_source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/preview")
def preview_sla(payload: SLAPreviewRequest, current_user=Depends(require_engineer)):
    """Preview SLA calculation without persisting (BE-009)."""
    result = calculate_sla(
        outage_id="PREVIEW",
        severity=payload.severity.value,
        mttr_minutes=payload.mttr_minutes,
        current_user=current_user,
    )
    return result


@router.get("/config")
def get_sla_config(
    include_hashes: bool = Query(
        default=False, description="Include policy version + content hash for integrity (#37)"
    ),
    current_user=Depends(require_engineer),
):
    """Get all SLA configuration by severity (BE-009).

    Set `include_hashes=true` to get policy_version, content_hash, and severity metadata (#37).
    """
    if include_hashes:
        return get_all_config_with_hashes()
    return get_all_config()


@router.get("/config/{severity}")
def get_sla_config_by_severity(
    severity: str,
    include_hash: bool = Query(default=False, description="Include policy version + content hash (#37)"),
    current_user=Depends(require_engineer),
):
    """Get SLA configuration for a specific severity (BE-009).

    Set `include_hash=true` to get policy_version, content_hash, and severity (#37).
    """
    try:
        if include_hash:
            return get_config_with_hash(severity)
        return get_config_for_severity(severity)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/config/{severity}")
def update_sla_config(
    severity: str,
    payload: SLAConfigUpdateRequest,
    expected_token: str | None = Query(default=None, description="Optimistic concurrency token (#37). Mismatch → 409."),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update SLA config for a severity with optional optimistic concurrency (#37).

    When `expected_token` is provided:
    - The update is atomic: policy_version is bumped, content_hash is computed,
      history is recorded in sla_config_history.
    - If another concurrent update has already occurred, returns 409 Conflict.

    Without `expected_token`, the update still records a version bump and a
    history entry (see #272).
    """
    try:
        if expected_token is not None:
            policy, new_token, _history = publish_config_for_severity(
                severity,
                payload,
                expected_token=expected_token,
                published_by=current_user.email,
                db=db,
            )
            return policy
        return update_config_for_severity(severity, payload, db=db)
    except ConcurrencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/config/{severity}/token")
def get_config_publish_token(
    severity: str,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get the current publish token for optimistic concurrency control (#37).

    Reads the latest persisted history row so a token fetched after a
    restart matches the token the publish path compares against (#272).
    """
    try:
        return {"severity": severity, "token": get_current_token(severity, db=db)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/analytics/dashboard", response_model=SLADashboardKPI)
def get_sla_dashboard_kpis(
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    site: str | None = Query(default=None, description="Alias for site_id"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    resolved_site = site_id or site
    cache_key = f"dashboard_kpis_{severity}_{resolved_site}"
    cached = _dashboard_cache.get(cache_key)
    if cached is not None:
        return cached
    repo = SLARepository(db)
    result = repo.aggregate_dashboard_kpis(severity=severity, site_id=resolved_site)
    _dashboard_cache.set(cache_key, result)
    return result


@router.get("/analytics/trends", response_model=list[SLATrendPoint])
def get_sla_trends(
    days: int = Query(default=7, ge=1, le=90),
    bucket: str = Query(default="day", description="Bucket interval: day, week, month"),
    tz: str = Query(default="UTC", description="IANA timezone name, e.g. America/New_York"),
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    site: str | None = Query(default=None, description="Alias for site_id"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    if bucket not in VALID_BUCKETS:
        raise HTTPException(
            status_code=400, detail=f"Invalid bucket '{bucket}'. Must be one of: {', '.join(VALID_BUCKETS)}"
        )

    resolved_site = site_id or site
    cache_key = f"trends_{days}_{bucket}_{tz}_{severity}_{resolved_site}"
    cached = _dashboard_cache.get(cache_key)
    if cached is not None:
        return cached

    repo = SLARepository(db)
    try:
        result = repo.aggregate_trends(limit_days=days, bucket=bucket, tz=tz, severity=severity, site_id=resolved_site)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _dashboard_cache.set(cache_key, result)
    return result


@router.get("/performance/aggregation", response_model=SLAPerformanceAggregation)
def aggregate_sla_performance(
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    site: str | None = Query(default=None, description="Alias for site_id"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Get SLA performance aggregation with optional date range filtering (BE-009)."""
    resolved_site = site_id or site
    if start_date and start_date.tzinfo is not None:
        start_date = start_date.astimezone(UTC).replace(tzinfo=None)
    if end_date and end_date.tzinfo is not None:
        end_date = end_date.astimezone(UTC).replace(tzinfo=None)

    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date cannot be after end_date")

    repo = SLARepository(db)
    return repo.aggregate_performance(
        start_date=start_date, end_date=end_date, severity=severity, site_id=resolved_site
    )


@router.post("/analytics/snapshot", response_model=SLAAnalyticsSnapshot, status_code=201)
def create_analytics_snapshot(
    snapshot_key: str = Query(default="global"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Materialize current SLA aggregates into a persistent snapshot (BE-009)."""
    repo = SLARepository(db)
    snapshot = repo.create_snapshot(snapshot_key=snapshot_key)
    _invalidate_analytics_cache()
    return snapshot


@router.get("/analytics/snapshot", response_model=SLAAnalyticsSnapshot)
def get_latest_analytics_snapshot(
    snapshot_key: str = Query(default="global"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Return the most recent pre-aggregated analytics snapshot (BE-009)."""
    repo = SLARepository(db)
    snapshot = repo.get_latest_snapshot(snapshot_key=snapshot_key)
    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot found for the given key")
    return snapshot


@router.post("/analytics/snapshot/rebuild", response_model=SLAAnalyticsSnapshot)
def rebuild_analytics_snapshot(
    snapshot_key: str = Query(default="global"),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Rebuild analytics snapshot from live data (BE-025).

    This endpoint:
    - Aggregates current SLA data from scratch
    - Creates a new snapshot row (preserves history)
    - Is idempotent - safe to call multiple times
    - Requires admin privileges

    Use this for reconciliation after migrations or data drift.
    """
    repo = SLARepository(db)
    snapshot = repo.rebuild_snapshot(snapshot_key=snapshot_key)
    _invalidate_analytics_cache()
    return snapshot


@router.get("/analytics/snapshot/reconcile")
def reconcile_analytics_snapshot(
    snapshot_key: str = Query(default="global"),
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reconcile snapshot with live data to detect drift (BE-025).

    This read-only endpoint:
    - Compares latest snapshot with current live aggregates
    - Reports any differences found
    - Provides rebuild recommendation if drift detected
    - Requires admin privileges

    Use this to verify snapshot integrity before/after operations.
    """
    repo = SLARepository(db)
    reconciliation = repo.reconcile_snapshots(snapshot_key=snapshot_key)
    return reconciliation


@router.get("/analytics/dashboard/export")
def export_dashboard_kpis(
    format: str = Query(default="json", description="Export format: json or csv"),
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    site: str | None = Query(default=None, description="Alias for site_id"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Export dashboard KPI data in JSON or CSV format."""
    resolved_site = site_id or site
    repo = SLARepository(db)
    kpi = repo.aggregate_dashboard_kpis(severity=severity, site_id=resolved_site)

    try:
        exported = export_dashboard_kpi(kpi, format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if format.lower() == "csv":
        return Response(content=exported, media_type="text/csv")
    return Response(content=exported, media_type="application/json")esponse(
            content=exported,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=sla_dashboard_kpi.csv"},
        )
    return exported


@router.get("/analytics/trends/export")
def export_sla_trends(
    format: str = Query(default="json", description="Export format: json or csv"),
    days: int = Query(default=7, ge=1, le=365, description="Number of days to export (max 365 for exports)"),
    bucket: str = Query(default="day", description="Bucket interval: day, week, month"),
    tz: str = Query(default="UTC", description="IANA timezone name, e.g. America/New_York"),
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    site: str | None = Query(default=None, description="Alias for site_id"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Export SLA trends data in JSON or CSV format."""
    if bucket not in VALID_BUCKETS:
        raise HTTPException(
            status_code=400, detail=f"Invalid bucket '{bucket}'. Must be one of: {', '.join(VALID_BUCKETS)}"
        )

    resolved_site = site_id or site
    repo = SLARepository(db)
    try:
        trends = repo.aggregate_trends(limit_days=days, bucket=bucket, tz=tz, severity=severity, site_id=resolved_site)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        exported = export_trends(trends, format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if format.lower() == "csv":
        return Response(
            content=exported,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=sla_trends_{days}d.csv"},
        )
    return exported


@router.get("/analytics/performance/export")
def export_performance_aggregation_endpoint(
    format: str = Query(default="json", description="Export format: json or csv"),
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    site: str | None = Query(default=None, description="Alias for site_id"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Export performance aggregation data in JSON or CSV format."""
    resolved_site = site_id or site
    if start_date and start_date.tzinfo is not None:
        start_date = start_date.astimezone(UTC).replace(tzinfo=None)
    if end_date and end_date.tzinfo is not None:
        end_date = end_date.astimezone(UTC).replace(tzinfo=None)

    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date cannot be after end_date")

    repo = SLARepository(db)
    aggregation = repo.aggregate_performance(
        start_date=start_date, end_date=end_date, severity=severity, site_id=resolved_site
    )

    try:
        exported = export_performance_aggregation(aggregation, format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if format.lower() == "csv":
        return Response(
            content=exported,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=sla_performance.csv"},
        )
    return exported


@router.get("/analytics/snapshot/verify")
def verify_snapshot_integrity(
    snapshot_key: str = Query(default="global"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Verify integrity of the latest analytics snapshot."""
    repo = SLARepository(db)
    result = repo.verify_snapshot_integrity(snapshot_key=snapshot_key)
    if not result["valid"]:
        raise HTTPException(status_code=409, detail=result.get("error", "Invalid snapshot"))
    return result


@router.get("/analytics/export")
def export_analytics_summary_endpoint(
    format: str = Query(default="json", description="Export format: json or csv"),
    days: int = Query(default=7, ge=1, le=365, description="Number of days for trends"),
    bucket: str = Query(default="day", description="Bucket interval: day, week, month"),
    tz: str = Query(default="UTC", description="IANA timezone name"),
    severity: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    site: str | None = Query(default=None, description="Alias for site_id"),
    include_aggregation: bool = Query(default=True, description="Include performance aggregation"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Export comprehensive analytics summary (KPI + trends + optional aggregation)."""
    if bucket not in VALID_BUCKETS:
        raise HTTPException(
            status_code=400, detail=f"Invalid bucket '{bucket}'. Must be one of: {', '.join(VALID_BUCKETS)}"
        )

    resolved_site = site_id or site
    repo = SLARepository(db)

    kpi = repo.aggregate_dashboard_kpis(severity=severity, site_id=resolved_site)

    try:
        trends = repo.aggregate_trends(limit_days=days, bucket=bucket, tz=tz, severity=severity, site_id=resolved_site)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    aggregation = None
    if include_aggregation:
        aggregation = repo.aggregate_performance(severity=severity, site_id=resolved_site)

    try:
        exported = export_analytics_summary(kpi, trends, aggregation, format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if format.lower() == "csv":
        return Response(
            content=exported,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=sla_analytics_summary_{days}d.csv"},
        )
    return exported
