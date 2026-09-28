"""Admin endpoints for contract governance operations.

Provides REST API for two-step admin and operator transfers on the
Soroban SLA calculator contract.  All endpoints require admin role.

Governance ops raise ``GovernanceNotImplementedError`` while there is no
real Soroban execution path (``GOVERNANCE_ENABLED`` off); the API surfaces
that as 501 with a ``not_implemented`` problem detail rather than a
fabricated success payload.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import require_admin
from app.services.audit_log import audit_log
from app.services.contracts.governance_client import (
    GovernanceError,
    GovernanceNotImplementedError,
    accept_admin,
    accept_operator,
    cancel_admin_proposal,
    propose_admin,
    propose_operator,
    renounce_admin,
)

router = APIRouter()


class ProposeAdminRequest(BaseModel):
    new_admin_address: str


class ProposeOperatorRequest(BaseModel):
    new_operator_address: str


@router.post("/propose-admin")
def api_propose_admin(
    payload: ProposeAdminRequest,
    current_user=Depends(require_admin),
):
    """Initiate a two-step admin transfer on the SLA contract."""
    try:
        result = propose_admin(payload.new_admin_address)
    except GovernanceNotImplementedError as exc:
        raise HTTPException(status_code=501, detail={"error": "not_implemented", "message": str(exc)}) from exc
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_log.log(
        event_type="governance_propose_admin",
        details={
            "actor": current_user.email,
            "new_admin": payload.new_admin_address,
            "tx_hash": result["tx_hash"],
        },
    )
    return result


@router.post("/accept-admin")
def api_accept_admin(
    current_user=Depends(require_admin),
):
    """Complete an admin transfer (called by the proposed new admin)."""
    try:
        result = accept_admin()
    except GovernanceNotImplementedError as exc:
        raise HTTPException(status_code=501, detail={"error": "not_implemented", "message": str(exc)}) from exc
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_log.log(
        event_type="governance_accept_admin",
        details={
            "actor": current_user.email,
            "tx_hash": result["tx_hash"],
        },
    )
    return result


@router.post("/cancel-admin-proposal")
def api_cancel_admin_proposal(
    current_user=Depends(require_admin),
):
    """Cancel a pending admin proposal."""
    try:
        result = cancel_admin_proposal()
    except GovernanceNotImplementedError as exc:
        raise HTTPException(status_code=501, detail={"error": "not_implemented", "message": str(exc)}) from exc
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_log.log(
        event_type="governance_cancel_admin_proposal",
        details={
            "actor": current_user.email,
            "tx_hash": result["tx_hash"],
        },
    )
    return result


@router.post("/renounce-admin")
def api_renounce_admin(
    current_user=Depends(require_admin),
):
    """Renounce admin role permanently."""
    try:
        result = renounce_admin()
    except GovernanceNotImplementedError as exc:
        raise HTTPException(status_code=501, detail={"error": "not_implemented", "message": str(exc)}) from exc
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_log.log(
        event_type="governance_renounce_admin",
        details={
            "actor": current_user.email,
            "tx_hash": result["tx_hash"],
        },
    )
    return result


@router.post("/propose-operator")
def api_propose_operator(
    payload: ProposeOperatorRequest,
    current_user=Depends(require_admin),
):
    """Initiate a two-step operator transfer on the SLA contract."""
    try:
        result = propose_operator(payload.new_operator_address)
    except GovernanceNotImplementedError as exc:
        raise HTTPException(status_code=501, detail={"error": "not_implemented", "message": str(exc)}) from exc
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_log.log(
        event_type="governance_propose_operator",
        details={
            "actor": current_user.email,
            "new_operator": payload.new_operator_address,
            "tx_hash": result["tx_hash"],
        },
    )
    return result


@router.post("/accept-operator")
def api_accept_operator(
    current_user=Depends(require_admin),
):
    """Complete an operator transfer (called by the proposed new operator)."""
    try:
        result = accept_operator()
    except GovernanceNotImplementedError as exc:
        raise HTTPException(status_code=501, detail={"error": "not_implemented", "message": str(exc)}) from exc
    except GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_log.log(
        event_type="governance_accept_operator",
        details={
            "actor": current_user.email,
            "tx_hash": result["tx_hash"],
        },
    )
    return result
