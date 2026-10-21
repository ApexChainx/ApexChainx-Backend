"""TxIndexer health probe (#714).

Served from the API process, which never runs the ingest loop itself: the probe
reads the worker's Redis heartbeat (see ``app/services/tx_indexer_health.py``)
and pings the ledger database, then reports ``ok`` / ``degraded`` / ``down``.
``down`` returns 503 so a load balancer or orchestrator can act on it;
``degraded`` stays 200 because a single failed pass (or a fresh deployment that
has not indexed yet) must not take the API out of rotation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine
from app.services.tx_indexer_health import (
    TxIndexerStateStore,
    build_indexer_health,
)

router = APIRouter()


class IndexerHealthComponent(BaseModel):
    status: str
    details: dict[str, Any] = Field(default_factory=dict)


class IndexerHealthResponse(BaseModel):
    status: str = Field(description="ok | degraded | down")
    reason: str | None = Field(default=None, description="Why the probe is not ok")
    observed_at: str
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    lag_seconds: float | None = Field(default=None, description="Seconds since the last successful pass")
    max_lag_seconds: int
    consecutive_failures: int
    last_error: str | None = None
    last_inserted: int
    components: dict[str, IndexerHealthComponent]


def get_state_store() -> TxIndexerStateStore:
    """Factory kept separate so tests can substitute an in-memory store."""
    return TxIndexerStateStore()


def _database_status() -> tuple[bool, str | None]:
    """Return (reachable, error). A probe failure is never raised to the caller."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:240]
    return True, None


@router.get(
    "/health",
    response_model=IndexerHealthResponse,
    summary="TxIndexer ingest health",
    description=(
        "Report whether the transaction indexer is keeping up. Reads the worker heartbeat from "
        "Redis and pings the ledger database. Returns 503 only when the database is unreachable; "
        "a stale or failing indexer is reported as 200 with status=degraded."
    ),
    responses={
        200: {"description": "Indexer is healthy or degraded"},
        503: {"description": "Ledger database unreachable"},
    },
)
def indexer_health(response: Response) -> IndexerHealthResponse:
    state = get_state_store().load()
    database_ok, database_error = _database_status()
    report = build_indexer_health(
        state,
        max_lag_seconds=settings.TX_INDEXER_HEALTH_MAX_LAG_SECONDS,
        database_ok=database_ok,
        database_error=database_error,
    )

    if report["status"] == "down":
        response.status_code = 503
    return IndexerHealthResponse(**report)
