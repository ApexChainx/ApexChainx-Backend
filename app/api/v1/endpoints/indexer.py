"""TxIndexer health probe (#714).

Served from the API process, which never runs the ingest loop itself: the probe
reads the worker's Redis heartbeat (see ``app/services/tx_indexer_health.py``)
and pings the ledger database, then reports ``ok`` / ``degraded`` / ``down``.
``down`` returns 503 so a load balancer or orchestrator can act on it;
``degraded`` stays 200 because a single failed pass (or a fresh deployment that
has not indexed yet) must not take the API out of rotation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.coded_errors import CodedHTTPException
from app.core.config import settings
from app.core.security import require_engineer
from app.db.session import engine, get_db
from app.repositories.tx_indexer_repository import TxIndexerRepository
from app.services.tx_indexer_health import (
    TxIndexerStateStore,
    build_indexer_health,
)
from app.services.tx_indexer_query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    TxIndexerQueryError,
)

router = APIRouter()


class IndexedTransactionResponse(BaseModel):
    """A row as stored by the ingest loop."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    transaction_hash: str
    type: str
    amount: int
    asset_code: str
    from_address: str
    to_address: str
    status: str
    outage_id: str | None = None
    created_at: datetime
    confirmed_at: datetime | None = None


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
    "/transactions",
    response_model=list[IndexedTransactionResponse],
    summary="Search indexed transactions",
    description=(
        "Query the transactions the indexer has stored. All filters are optional and every "
        "value is validated before any SQL is prepared: hashes must be 64-hex, accounts must "
        "be valid Stellar keys, statuses/sort keys come from fixed whitelists, and amounts are "
        "bounded integers. Invalid filters return 422 with the reason."
    ),
    responses={
        200: {"description": "Matching transactions, newest first by default"},
        422: {"description": "A filter value is invalid"},
    },
)
def list_indexed_transactions(
    transaction_hash: str | None = Query(default=None, max_length=64),
    account: str | None = Query(default=None, max_length=56, description="Matches from_address or to_address"),
    asset_code: str | None = Query(default=None, max_length=12),
    status: str | None = Query(default=None, max_length=16),
    type: str | None = Query(default=None, max_length=32),
    outage_id: str | None = Query(default=None, max_length=64),
    min_amount: int | None = Query(default=None, ge=0, description="Stroops"),
    max_amount: int | None = Query(default=None, ge=0, description="Stroops"),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    sort_by: str = Query(default="created_at", max_length=32),
    sort_dir: str = Query(default="desc", max_length=4),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
) -> list[IndexedTransactionResponse]:
    """Search the indexer's view of the chain (#716)."""
    try:
        rows = TxIndexerRepository(db).search(
            transaction_hash=transaction_hash,
            account=account,
            asset_code=asset_code,
            status=status,
            transaction_type=type,
            outage_id=outage_id,
            min_amount=min_amount,
            max_amount=max_amount,
            created_after=created_after,
            created_before=created_before,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
        )
    except TxIndexerQueryError as exc:
        raise CodedHTTPException(status_code=422, detail=str(exc)) from exc

    return [IndexedTransactionResponse.model_validate(row) for row in rows]


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
