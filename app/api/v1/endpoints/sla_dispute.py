from datetime import datetime
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.sla_dispute import DisputeAuditLog, SLADispute, DisputeStatus
from app.models.orm.sla import SLAResultORM
from app.schemas.sla_dispute import (
    DisputeAuditLogResponse,
    DisputeFlagRequest,
    DisputeResolveRequest,
    DisputeResponse,
    CreateProposedSLARequest,
)
from app.core.security import require_engineer, require_admin
from app.services.metrics import (
    increment_counter,
    record_histogram,
    SLADISPUTE_NOTIFICATION_ATTEMPT_TOTAL,
    SLADISPUTE_NOTIFICATION_DURATION_MS,
)
from app.services.sla.sla_calculator import SLACalculator
from app.repositories.sla_repository import SLARepository

router = APIRouter()


@router.get(
    "/disputes",
    response_model=list[DisputeResponse],
    summary="List SLA disputes",
)
def list_disputes(
    status_filter: DisputeStatus | None = Query(default=None, alias="status"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    query = db.query(SLADispute).order_by(SLADispute.flagged_at.desc())
    if status_filter is not None:
        query = query.filter(SLADispute.status == status_filter)
    return query.all()


@router.post(
    "/{sla_result_id}/dispute",
    response_model=DisputeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Flag an SLA result for dispute",
)
def flag_dispute(
    sla_result_id: int,
    payload: DisputeFlagRequest,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    # Check if SLA result exists
    sla_result = db.query(SLAResultORM).filter(SLAResultORM.id == sla_result_id).first()
    if not sla_result:
        raise HTTPException(status_code=404, detail="SLA result not found")

    existing = (
        db.query(SLADispute)
        .filter(
            SLADispute.sla_result_id == sla_result_id,
            SLADispute.status == DisputeStatus.PENDING,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active dispute already exists for this SLA result.",
        )

    dispute = SLADispute(
        sla_result_id=sla_result_id,
        baseline_sla_result_id=sla_result_id,
        flagged_by=payload.flagged_by,
        dispute_reason=payload.dispute_reason,
    )
    db.add(dispute)
    db.flush()

    db.add(DisputeAuditLog(
        dispute_id=dispute.id,
        action="flagged",
        actor=payload.flagged_by,
        notes=payload.dispute_reason,
    ))
    db.commit()
    db.refresh(dispute)
    return dispute


@router.post(
    "/{sla_result_id}/dispute/proposed",
    response_model=DisputeResponse,
    summary="Create a proposed SLA result for a pending dispute",
)
def create_proposed_sla(
    sla_result_id: int,
    payload: CreateProposedSLARequest,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    dispute = (
        db.query(SLADispute)
        .filter(
            SLADispute.sla_result_id == sla_result_id,
            SLADispute.status == DisputeStatus.PENDING,
        )
        .first()
    )
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending dispute found for this SLA result.",
        )
    
    # Get baseline SLA to get outage_id
    baseline_sla = db.query(SLAResultORM).filter(SLAResultORM.id == dispute.baseline_sla_result_id).first()
    if not baseline_sla:
        raise HTTPException(status_code=404, detail="Baseline SLA not found")

    # Calculate new proposed SLA
    new_sla = SLACalculator.calculate(
        outage_id=baseline_sla.outage_id,
        severity=payload.severity,
        mttr_minutes=payload.mttr_minutes,
        policy_version=payload.policy_version,
        threshold_source=payload.threshold_source,
    )

    # Save proposed SLA (but don't mark as latest yet)
    repo = SLARepository(db)
    proposed_sla_orm = SLAResultORM(
        outage_id=new_sla.outage_id,
        status=new_sla.status,
        mttr_minutes=new_sla.mttr_minutes,
        threshold_minutes=new_sla.threshold_minutes,
        amount=new_sla.amount,
        payment_type=new_sla.payment_type,
        rating=new_sla.rating,
        policy_version=new_sla.policy_version,
        threshold_source=new_sla.threshold_source,
        reason_code=new_sla.reason_code,
        decision_trace=new_sla.decision_trace,
        is_latest=False,  # Don't mark as latest yet
    )
    db.add(proposed_sla_orm)
    db.flush()

    # Update dispute with proposed SLA
    dispute.proposed_sla_result_id = proposed_sla_orm.id

    # Add audit log
    audit_notes = f"Proposed SLA created: {json.dumps(new_sla.model_dump())}"
    if payload.notes:
        audit_notes += f" | Notes: {payload.notes}"
    db.add(DisputeAuditLog(
        dispute_id=dispute.id,
        action="proposed_sla_created",
        actor=payload.created_by,
        notes=audit_notes,
    ))
    db.commit()
    db.refresh(dispute)
    return dispute


@router.put(
    "/{sla_result_id}/dispute/resolve",
    response_model=DisputeResponse,
    summary="Resolve or reject an SLA dispute",
)
def resolve_dispute(
    sla_result_id: int,
    payload: DisputeResolveRequest,
    current_user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.status not in (DisputeStatus.RESOLVED, DisputeStatus.REJECTED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resolution status must be 'resolved' or 'rejected'.",
        )

    dispute = (
        db.query(SLADispute)
        .filter(
            SLADispute.sla_result_id == sla_result_id,
            SLADispute.status == DisputeStatus.PENDING,
        )
        .first()
    )
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending dispute found for this SLA result.",
        )

    dispute.status = payload.status
    dispute.resolved_by = payload.resolved_by
    dispute.resolution_notes = payload.resolution_notes
    dispute.resolved_at = datetime.utcnow()

    # If resolving and apply_proposed is true, mark the proposed SLA as latest
    if payload.status == DisputeStatus.RESOLVED and payload.apply_proposed:
        if not dispute.proposed_sla_result_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No proposed SLA result to apply.",
            )
        repo = SLARepository(db)
        proposed_sla = db.query(SLAResultORM).filter(SLAResultORM.id == dispute.proposed_sla_result_id).first()
        if not proposed_sla:
            raise HTTPException(status_code=404, detail="Proposed SLA not found")
        
        # Demote existing latest
        existing_latest = (
            db.query(SLAResultORM)
            .filter(SLAResultORM.outage_id == proposed_sla.outage_id, SLAResultORM.is_latest.is_(True))
            .with_for_update()
            .first()
        )
        if existing_latest:
            existing_latest.is_latest = False
        
        # Mark proposed as latest
        proposed_sla.is_latest = True
        db.add(proposed_sla)

    db.add(DisputeAuditLog(
        dispute_id=dispute.id,
        action=payload.status.value,
        actor=payload.resolved_by,
        notes=payload.resolution_notes,
    ))
    db.commit()
    db.refresh(dispute)

    increment_counter(SLADISPUTE_NOTIFICATION_ATTEMPT_TOTAL, tags={"status": payload.status.value})
    return dispute


@router.get(
    "/{sla_result_id}/dispute",
    response_model=DisputeResponse,
    summary="Get the dispute for an SLA result",
)
def get_dispute(
    sla_result_id: int,
    db: Session = Depends(get_db),
):
    dispute = (
        db.query(SLADispute)
        .filter(SLADispute.sla_result_id == sla_result_id)
        .order_by(SLADispute.flagged_at.desc())
        .first()
    )
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No dispute found for this SLA result.",
        )
    return dispute


@router.get(
    "/{sla_result_id}/dispute/history",
    response_model=list[DisputeAuditLogResponse],
    summary="Get audit trail for a dispute",
)
def get_dispute_history(
    sla_result_id: int,
    db: Session = Depends(get_db),
):
    dispute = (
        db.query(SLADispute)
        .filter(SLADispute.sla_result_id == sla_result_id)
        .order_by(SLADispute.flagged_at.desc())
        .first()
    )
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No dispute found for this SLA result.",
        )
    return dispute.audit_logs
