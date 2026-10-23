from fastapi import APIRouter, Depends

from app.core.security import require_admin
from app.services.audit_log import audit_log

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def get_audit_log(current_user=Depends(require_admin)):
    return audit_log.list()


@router.get("/verify")
def verify_audit_chain(current_user=Depends(require_admin)):
    return audit_log.verify_chain()
