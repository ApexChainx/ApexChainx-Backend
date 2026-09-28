from fastapi import APIRouter, Depends, Query

from app.core.security import require_admin
from app.schemas.pagination import MAX_PAGE_SIZE
from app.services.audit_log import audit_log

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def get_audit_log(
    limit: int = Query(
        default=50,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Max entries per page (1-{MAX_PAGE_SIZE}).",
    ),
    offset: int = Query(default=0, ge=0, description="Number of entries to skip."),
    current_user=Depends(require_admin),
):
    # #564: GET /audit used to dump the entire table. It now pages with the
    # same shared cap as every other list endpoint (422 above MAX_PAGE_SIZE).
    return audit_log.list(limit=limit, offset=offset)


@router.get("/verify")
def verify_audit_chain(current_user=Depends(require_admin)):
    return audit_log.verify_chain()
