import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.v1.endpoints.sla import _invalidate_analytics_cache
from app.core.config import settings
from app.core.lock import ConcurrencyLockError, advisory_lock_nowait
from app.core.security import require_admin, require_engineer
from app.db.session import get_db
from app.models import BulkOutageCreate, Outage, OutageCreate, OutageUpdate
from app.models.enums import OutageStatus, Severity
from app.models.outage import PaginatedOutages, ResolveOutageRequest
from app.models.outage_dto import (
    ImportConsistency,
    ImportFieldError,
    ImportResponse,
    ImportRowResult,
    OutageSortDirection,
    OutageSortField,
)
from app.models.webhook import WebhookEvent
from app.repositories.outage_event_repository import OutageEventRepository
from app.repositories.outage_repository import OutageRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.sla_repository import SLARepository
from app.services.audit_log import audit_log
from app.services.contracts import SLAContractAdapter, translate_contract_result
from app.services.webhook_service import trigger_sla_violation_webhooks
from app.utils.exporter import export_outages

router = APIRouter()


@router.get("/export")
def export_outages_endpoint(
    format: str = "json",
    severity: Severity | None = None,
    status: OutageStatus | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    repo = OutageRepository(db)
    data = repo.list_filtered(
        severity=severity,
        status=status,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )
    try:
        exported = export_outages(data, format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if format == "csv":
        return Response(
            content=exported,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=outages.csv"},
        )
    return exported


@router.get("/violations")
def list_violations(current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    return repo.list_violations()


@router.get("/", response_model=PaginatedOutages)
def list_outages(
    severity: Severity | None = None,
    status: OutageStatus | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: OutageSortField = Query(
        default=OutageSortField.detected_at,
        description="Sort field (enum). Supported: detected_at, site_name, severity, status, id. Invalid values rejected with 422.",
    ),
    sort_direction: OutageSortDirection = Query(
        default=OutageSortDirection.desc,
        description="Sort direction (enum). Supported: asc, desc. Invalid values rejected with 422. Default: desc.",
    ),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """List outages with optional filtering, search, and sorting.

    **Search (BE-011)**
    - `search`: case-insensitive substring match across `id`, `site_id`, and `site_name`
    - Composes with all other filters (severity, status, date range) and pagination
    - Exact and partial matches are both supported
    - No match returns 0 items with total=0

    **Sorting Contract (BE-012)**
    - Supported sort fields (all validated):
      - detected_at: Time the outage was detected (default, stable)
      - site_name: Name of the affected site
      - severity: Outage severity (critical, high, medium, low)
      - status: Outage status (open, resolved)
      - id: Unique outage identifier
    - Default sorting: detected_at descending, then id ascending (stable, deterministic)
    - Invalid sort values: rejected with 422 validation error
    """
    repo = OutageRepository(db)
    return repo.list(
        severity=severity,
        status=status,
        search=search,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )


@router.get("/{outage_id}", response_model=Outage)
def get_outage(outage_id: str, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    outage = repo.get(outage_id)
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")
    return outage


@router.post("/", response_model=Outage)
def create_outage(payload: OutageCreate, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    # Creation is idempotent when the same outage payload is submitted again.
    # Duplicate outage payloads are detected and the existing outage is returned.
    repo = OutageRepository(db)
    try:
        outage = repo.create(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_log.log("outage_created", {"id": outage.id})
    OutageEventRepository(db).record(outage.id, "created", {"site_name": outage.site_name})
    return outage


@router.post("/bulk", response_model=dict)
def bulk_create_outages(
    payload: BulkOutageCreate, current_user=Depends(require_engineer), db: Session = Depends(get_db)
):
    repo = OutageRepository(db)
    items: list[Outage] = []
    persisted_count = 0
    try:
        for outage_payload in payload.outages:
            created, persisted = repo.create_or_get_existing(outage_payload)
            items.append(created)
            if persisted:
                persisted_count += 1
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"count": len(items), "persisted": persisted_count, "items": items}


# Duplicate detection is explicit and consistent for imports:
# - same site_name, detected_at, description, and optional site_id are treated as the same outage
# - duplicate rows are reported as duplicate and do not create additional persisted rows
@router.post(
    "/import",
    response_model=ImportResponse,
    summary="Bulk import outages from CSV or JSON file with optional dry-run validation and explicit consistency mode",
)
async def import_outages(
    file: UploadFile = File(...),
    dry_run: bool = Query(
        default=False,
        description="Validation-only mode: validate all rows WITHOUT persisting to database. Returns the same field/row-level errors as live imports.",
    ),
    consistency: ImportConsistency = Query(
        default=ImportConsistency.atomic,
        description="Write consistency mode for live imports. atomic=all-or-nothing, partial=persist valid rows and report row-level failures.",
    ),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    MAX_BYTES = settings.MAX_FILE_UPLOAD_SIZE_BYTES
    CHUNK_SIZE = 64 * 1024  # 64 KB

    filename = file.filename or ""

    # --- #213: chunked read with size cap ---
    chunks: list[bytes] = []
    total_read = 0
    while True:
        chunk = await file.read(CHUNK_SIZE)
        if not chunk:
            break
        total_read += len(chunk)
        if total_read > MAX_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds 10 MB limit")
        chunks.append(chunk)
    content = b"".join(chunks)

    # --- parse into a row iterator (avoids holding two full copies in memory) ---
    if filename.endswith(".json"):
        try:
            rows = json.loads(content)
            if not isinstance(rows, list):
                raise HTTPException(status_code=400, detail="JSON file must contain a list of outage objects")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
    elif filename.endswith(".csv"):
        try:
            rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid CSV: {exc}") from exc
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Use .json or .csv")

    if len(rows) > settings.MAX_BULK_OUTAGES_COUNT:
        raise HTTPException(
            status_code=400, detail=f"Too many rows in file. Maximum allowed is {settings.MAX_BULK_OUTAGES_COUNT}."
        )

    repo = OutageRepository(db)
    row_outcomes: list[ImportRowResult] = []
    persisted_count = 0

    if dry_run:
        # --- BE-012: Dry-run validation mode ---
        # Validates rows exactly as live import: OutageCreate field validation + duplicate detection
        # Returns field and row-level errors with same semantics, but does NOT persist
        for i, row in enumerate(rows):
            try:
                payload = OutageCreate(**row)  # Full field validation via Pydantic
                duplicate = repo.check_duplicate(payload)  # Duplicate detection same as live import
                if duplicate:
                    row_outcomes.append(
                        ImportRowResult(
                            row=i,
                            id=payload.id,
                            status="ok",
                            duplicate=True,
                            existing_id=duplicate.id,
                        )
                    )
                else:
                    row_outcomes.append(
                        ImportRowResult(
                            row=i,
                            id=payload.id,
                            status="ok",
                            duplicate=False,
                        )
                    )
            except Exception as exc:
                row_outcomes.append(_row_error(i, row, exc))
    elif consistency == ImportConsistency.atomic:
        parsed: list[OutageCreate] = []
        for i, row in enumerate(rows):
            try:
                parsed.append(OutageCreate(**row))
                row_outcomes.append(ImportRowResult(row=i, id=row.get("id"), status="ok"))
            except Exception as exc:
                row_outcomes.append(_row_error(i, row, exc))

        if any(r.status == "error" for r in row_outcomes):
            return _import_response("import", consistency, len(rows), 0, row_outcomes)

        try:
            for i, payload in enumerate(parsed):
                created, persisted = repo.create_or_get_existing(payload)
                row_outcomes[i].outage_id = created.id
                row_outcomes[i].persisted = persisted
                if persisted:
                    persisted_count += 1
                else:
                    row_outcomes[i].duplicate = True
                    row_outcomes[i].existing_id = created.id
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Transaction failed: {exc}") from exc
    else:
        for i, row in enumerate(rows):
            try:
                payload = OutageCreate(**row)
                created, persisted = repo.create_or_get_existing(payload)
                row_outcomes.append(
                    ImportRowResult(
                        row=i,
                        id=payload.id,
                        status="ok",
                        outage_id=created.id,
                        persisted=persisted,
                        duplicate=not persisted,
                        existing_id=created.id if not persisted else None,
                    )
                )
                if persisted:
                    persisted_count += 1
            except Exception as exc:
                db.rollback()
                row_outcomes.append(_row_error(i, row, exc))

    return _import_response("dry_run" if dry_run else "import", consistency, len(rows), persisted_count, row_outcomes)


def _row_error(index: int, raw_row: dict, exc: Exception) -> ImportRowResult:
    """Return a stable machine-readable ImportRowResult for a failed row."""
    errors: list[ImportFieldError] = []
    if hasattr(exc, "errors"):
        for e in exc.errors():  # type: ignore[union-attr]
            errors.append(
                ImportFieldError(
                    field=".".join(str(loc) for loc in e["loc"]) if e.get("loc") else None,
                    type=e.get("type"),
                    message=e.get("msg", str(e)),
                )
            )
    else:
        errors.append(ImportFieldError(field=None, type=type(exc).__name__, message=str(exc)))

    return ImportRowResult(row=index, id=raw_row.get("id"), status="error", errors=errors)


def _import_response(
    mode: str, consistency: ImportConsistency, total: int, persisted: int, outcomes: list[ImportRowResult]
) -> ImportResponse:
    error_rows = [r for r in outcomes if r.status == "error"]
    return ImportResponse(
        mode=mode,
        consistency=consistency,
        total_rows=total,
        persisted=persisted,
        validated=sum(1 for r in outcomes if r.status == "ok"),
        error_count=len(error_rows),
        errors=error_rows,
        rows=outcomes,
    )


@router.put("/{outage_id}", response_model=Outage)
def update_outage(
    outage_id: str, payload: OutageUpdate, current_user=Depends(require_engineer), db: Session = Depends(get_db)
):
    repo = OutageRepository(db)
    existing = repo.get(outage_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Outage not found")

    try:
        updated = repo.update(outage_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    OutageEventRepository(db).record(outage_id, "updated", payload.model_dump(exclude_unset=True, exclude_none=True))
    return updated


@router.patch("/{outage_id}", response_model=Outage)
def patch_outage(
    outage_id: str, payload: OutageUpdate, current_user=Depends(require_engineer), db: Session = Depends(get_db)
):
    """Partially update an outage with status transition validation (BE-013).

    Enforced transitions:
    - open -> open (idempotent)
    - open -> resolved (permitted)
    - resolved -> resolved (idempotent)
    - Other transitions: 400 Bad Request
    """
    repo = OutageRepository(db)
    existing = repo.get(outage_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Outage not found")

    try:
        updated = repo.update(outage_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    OutageEventRepository(db).record(outage_id, "patched", payload.model_dump(exclude_unset=True, exclude_none=True))
    return updated


@router.delete("/{outage_id}")
def delete_outage(outage_id: str, current_user=Depends(require_admin), db: Session = Depends(get_db)):
    repo = OutageRepository(db)
    existing = repo.get(outage_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Outage not found")

    repo.delete(outage_id)
    return {"message": "Outage deleted successfully"}


@router.post("/{outage_id}/resolve")
def resolve_outage(
    outage_id: str, payload: ResolveOutageRequest, current_user=Depends(require_engineer), db: Session = Depends(get_db)
):
    """Resolve an outage, compute SLA, and create payment (BE-013).

    Status transition validation:
    - open -> resolved (permitted)
    - resolved -> resolved (idempotent if mttr_minutes matches)
    - Other transitions: 400 Bad Request

    Concurrency protection (BE-022):
    - Uses PostgreSQL advisory locks to prevent duplicate/concurrent resolutions
    - Returns 409 Conflict if another resolution is already in progress

    Also calculates SLA metrics and triggers webhook notifications.
    """
    repo = OutageRepository(db)

    # Acquire advisory lock to prevent concurrent resolutions
    try:
        with advisory_lock_nowait(db, f"resolve:{outage_id}"):
            try:
                outage = repo.resolve(outage_id, payload.mttr_minutes)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not outage:
                raise HTTPException(status_code=404, detail="Outage not found")

            audit_log.log("outage_resolved", {"id": outage.id, "mttr": payload.mttr_minutes})
            OutageEventRepository(db).record(outage_id, "resolved", {"mttr_minutes": payload.mttr_minutes})

            raw_contract_result = SLAContractAdapter.calculate_sla(
                outage_id=outage.id,
                severity=outage.severity,
                mttr_minutes=payload.mttr_minutes,
                policy_version="1.0",
                threshold_source="config",
            )
            sla = translate_contract_result(raw_contract_result)

            sla_repo = SLARepository(db)
            stored_sla = sla_repo.create_if_changed(sla)
            _invalidate_analytics_cache()
            OutageEventRepository(db).record(outage_id, "sla_computed", {"status": stored_sla.status})
            payment_repo = PaymentRepository(db)
            payment = payment_repo.create_for_sla_result(outage.id, stored_sla)
            webhook_event = WebhookEvent.SLA_VIOLATION if stored_sla.status == "violated" else WebhookEvent.SLA_RESOLVED
            trigger_sla_violation_webhooks(
                db,
                sla_data={"outage_id": outage.id, "sla": stored_sla.model_dump(), "payment": payment.model_dump()},
                event=webhook_event,
            )

            return {"outage": outage, "sla": stored_sla, "payment": payment}
    except ConcurrencyLockError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{outage_id}/recompute-sla")
def recompute_sla(outage_id: str, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    """Recompute SLA for a resolved outage (BE-013, BE-009).

    Status validation:
    - Only 'resolved' outages can have SLA recomputed
    - Returns 400 if outage not resolved

    Concurrency protection (BE-022):
    - Uses PostgreSQL advisory locks to prevent duplicate/concurrent recomputations
    - Returns 409 Conflict if another recomputation is already in progress

    Authorization: requires engineer role
    """
    repo = OutageRepository(db)
    outage = repo.get(outage_id)
    if not outage:
        raise HTTPException(status_code=404, detail="Outage not found")

    if outage.status != OutageStatus.resolved.value:
        raise HTTPException(status_code=400, detail="Outage must be resolved to recompute SLA")

    # Acquire advisory lock to prevent concurrent recomputations
    try:
        with advisory_lock_nowait(db, f"recompute:{outage_id}"):
            orm = repo.get_orm_locked(outage_id)
            raw_contract_result = SLAContractAdapter.calculate_sla(
                outage_id=outage.id,
                severity=outage.severity,
                mttr_minutes=orm.mttr_minutes,
                policy_version="1.0",
                threshold_source="config",
            )
            sla = translate_contract_result(raw_contract_result)

            sla_repo = SLARepository(db)
            stored_sla = sla_repo.create_if_changed(sla)
            _invalidate_analytics_cache()
            payment_repo = PaymentRepository(db)
            payment = payment_repo.create_for_sla_result(outage.id, stored_sla)
            webhook_event = WebhookEvent.SLA_VIOLATION if stored_sla.status == "violated" else WebhookEvent.SLA_RESOLVED
            trigger_sla_violation_webhooks(
                db,
                sla_data={"outage_id": outage.id, "sla": stored_sla.model_dump(), "payment": payment.model_dump()},
                event=webhook_event,
            )

            audit_log.log("sla_recomputed", {"id": outage.id})
            OutageEventRepository(db).record(outage_id, "sla_recomputed", {"status": stored_sla.status})
            return {"sla": stored_sla, "payment": payment}
    except ConcurrencyLockError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{outage_id}/timeline")
def get_outage_timeline(
    outage_id: str,
    event_type: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    """Get event timeline for an outage (BE-009)."""
    repo = OutageRepository(db)
    if not repo.get(outage_id):
        raise HTTPException(status_code=404, detail="Outage not found")
    return OutageEventRepository(db).list_for_outage(
        outage_id,
        event_type=event_type,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )
