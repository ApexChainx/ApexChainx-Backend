"""Health endpoint for the block poller (issue #730).

The poller has one failure mode that matters: a backlog of payments that
never drains. The endpoint answers "is the poller keeping up?" without
exposing anything about the payments themselves -- only aggregate numbers.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.block_poller_health import build_block_poller_report

router = APIRouter(prefix="/block-poller", tags=["block-poller"])


@router.get("/health")
def block_poller_health(db: Session = Depends(get_db)):
    """Report whether the block poller is keeping up with the backlog.

    Returns 200 while the poller is healthy or degraded, and 503 when its
    own bookkeeping cannot be read at all. The body carries aggregate
    numbers only: how many payments are still waiting for their confirmation
    and how old the oldest one is.
    """
    report = build_block_poller_report(db)
    report["timestamp"] = datetime.now(UTC).isoformat()

    if report["status"] == "down":
        return JSONResponse(status_code=503, content=report)
    return JSONResponse(status_code=200, content=report)
