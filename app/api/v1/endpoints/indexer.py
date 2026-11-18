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
from app.services.tx_indexer_input import TxIndexerValidationError, TxIndexFilters, sanitize_transaction_hash
from app.services.tx_indexer_query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    TxIndexerQueryError,
    validate_timestamp_range,
)
from app.utils.cursor import decode_cursor, encode_cursor

router = APIRouter()


class IndexedTransactionResponse(BaseModel):
    """A row as stored by the ingest loop."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "txn-1",
                "transaction_hash": "a" * 64,
                "type": "payment",
                "amount": 10000000,
                "asset_code": "USDC",
                "from_address": "GFROM",
                "to_address": "GTO",
                "status": "confirmed",
                "outage_id": None,
                "created_at": "2026-10-01T12:00:00Z",
                "confirmed_at": "2026-10-01T12:00:05Z",
            }
        },
    )

    id: str = Field(description="Stable identifier of the indexed transaction row")
    transaction_hash: str = Field(description="Stellar transaction hash (64 lowercase hex characters)")
    type: str = Field(description="Stellar operation type recorded for this transaction")
    amount: int = Field(description="Operation amount in stroops (1 XLM = 10,000,000 stroops)")
    asset_code: str = Field(description="Asset code of the transferred amount")
    from_address: str = Field(description="Source Stellar account of the operation")
    to_address: str = Field(description="Destination Stellar account of the operation")
    status: str = Field(description="Confirmation status recorded by the indexer")
    outage_id: str | None = Field(default=None, description="Related outage identifier, when one exists")
    created_at: datetime = Field(description="UTC time the transaction was observed on-chain")
    confirmed_at: datetime | None = Field(default=None, description="UTC time the transaction reached finality")


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


class IndexedTransactionPage(BaseModel):
    items: list[IndexedTransactionResponse] = Field(description="Transactions in the requested page")
    next_cursor: str | None = Field(default=None, description="Cursor for the next page, when one exists")
    has_more: bool = Field(default=False, description="Whether more matching transactions follow this page")


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


def _decode_after_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    pos = decode_cursor(cursor)
    if pos is None:
        raise CodedHTTPException(status_code=422, detail="Invalid cursor")
    try:
        after_ts = datetime.fromisoformat(str(pos[1]))
        return (after_ts, str(pos[0]))
    except (ValueError, TypeError) as exc:
        raise CodedHTTPException(status_code=422, detail="Invalid cursor") from exc


@router.get(
    "/transactions",
    response_model=IndexedTransactionPage,
    summary="Search indexed transactions",
    description=(
        "Query the transactions the indexer has stored. All filters are optional and every "
        "value is validated before any SQL is prepared. Invalid filters return 422."
    ),
    operation_id="listIndexedTransactions",
    responses={
        200: {"description": "Matching transactions, newest first by default"},
        401: {"description": "Authentication credentials are missing or invalid"},
        403: {"description": "The caller lacks the engineer role"},
        422: {"description": "A filter value is invalid"},
    },
)
def list_indexed_transactions(
    transaction_hash: str | None = Query(
        default=None, max_length=64, description="Exact Stellar transaction hash to match"
    ),
    account: str | None = Query(default=None, max_length=56, description="Matches from_address or to_address"),
    asset_code: str | None = Query(default=None, max_length=12, description="Asset code carried by the transaction"),
    status: str | None = Query(default=None, max_length=16, description="Confirmation status recorded by the indexer"),
    type: str | None = Query(default=None, max_length=32, description="Stellar operation type"),
    outage_id: str | None = Query(default=None, max_length=64, description="Related outage identifier"),
    min_amount: int | None = Query(default=None, ge=0, description="Stroops"),
    max_amount: int | None = Query(default=None, ge=0, description="Stroops"),
    created_after: datetime | None = Query(
        default=None, description="Only include transactions observed at or after this UTC timestamp"
    ),
    created_before: datetime | None = Query(
        default=None, description="Only include transactions observed before this UTC timestamp"
    ),
    sort_by: str = Query(default="created_at", max_length=32, description="Column used to order the result set"),
    sort_dir: str = Query(default="desc", max_length=4, description="Sort direction: asc or desc"),
    limit: int = Query(
        default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Maximum number of transactions per page"
    ),
    offset: int = Query(default=0, ge=0, description="Number of matching rows to skip when no cursor is supplied"),
    cursor: str | None = Query(default=None, description="Opaque cursor returned by the previous page"),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
) -> IndexedTransactionPage:
    """Search the indexer's view of the chain."""
    try:
        filters = TxIndexFilters.from_query(
            transaction_hash=transaction_hash,
            account=account,
            asset_code=asset_code,
            status=status,
            transaction_type=type,
            created_after=created_after,
            created_before=created_before,
        )
        validate_timestamp_range(filters.created_after, filters.created_before)
        rows = _query_rows(db, filters, outage_id, min_amount, max_amount, sort_by, sort_dir, limit, offset, cursor)
    except TxIndexerValidationError as exc:
        raise CodedHTTPException(status_code=422, detail=f"{exc.field}: {exc.reason}") from exc
    except TxIndexerQueryError as exc:
        raise CodedHTTPException(status_code=422, detail=str(exc)) from exc
    return _page_from_rows(rows, limit)


def _query_rows(db, filters, outage_id, min_amount, max_amount, sort_by, sort_dir, limit, offset, cursor):
    return TxIndexerRepository(db).list_indexed(
        transaction_hash=filters.transaction_hash,
        account=filters.account,
        asset_code=filters.asset_code,
        status=filters.status,
        transaction_type=filters.transaction_type,
        outage_id=outage_id,
        min_amount=min_amount,
        max_amount=max_amount,
        created_after=filters.created_after,
        created_before=filters.created_before,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit + 1,
        offset=(0 if cursor else offset),
        after=_decode_after_cursor(cursor),
    )


def _page_from_rows(rows, limit: int) -> IndexedTransactionPage:
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        ts = last.created_at.isoformat() if hasattr(last.created_at, "isoformat") else str(last.created_at)
        next_cursor = encode_cursor(last.id, ts)
    return IndexedTransactionPage(
        items=[IndexedTransactionResponse.model_validate(r) for r in page_rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/transactions/{transaction_hash}",
    response_model=IndexedTransactionResponse,
    summary="Get indexed transaction by hash",
    description="Return the single indexed transaction matching a Stellar transaction hash.",
    operation_id="getIndexedTransaction",
    responses={
        200: {"description": "The indexed transaction"},
        401: {"description": "Authentication credentials are missing or invalid"},
        403: {"description": "The caller lacks the engineer role"},
        404: {"description": "Transaction not present in the index"},
        422: {"description": "Invalid transaction hash"},
    },
)
def get_indexed_transaction(
    transaction_hash: str,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
) -> IndexedTransactionResponse:
    try:
        normalized = sanitize_transaction_hash(transaction_hash)
        if not normalized:
            raise TxIndexerValidationError("transaction_hash", "must not be empty")
    except TxIndexerValidationError as exc:
        raise CodedHTTPException(status_code=422, detail=f"{exc.field}: {exc.reason}") from exc
    row = TxIndexerRepository(db).get_by_hash(normalized)
    if row is None:
        raise CodedHTTPException(status_code=404, detail="Indexed transaction not found")
    return IndexedTransactionResponse.model_validate(row)


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
