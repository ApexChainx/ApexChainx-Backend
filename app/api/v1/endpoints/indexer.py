"""TxIndexer read API (#710).

The indexer's write path lives in :mod:`app.services.tx_indexer` and
:mod:`app.repositories.tx_indexer_repository`. This module is the read side:
the two routes below expose what the indexer has stored, so partners do not
have to scrape Horizon themselves.

Both routes are documented for OpenAPI/Swagger generation on purpose:

* every parameter carries a description and its bounds, so the generated
  Swagger UI shows an operator exactly what may be sent;
* every response the handler can produce is declared with ``responses``,
  including the 404 on the single-transaction route and the 422 raised when a
  query value fails validation;
* the response model carries a request example, so "Try it out" in Swagger UI
  starts from a realistic payload instead of an empty object.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.coded_errors import CodedHTTPException
from app.core.security import require_engineer
from app.db.session import get_db
from app.models.orm.payment import PaymentTransactionORM
from app.repositories.tx_indexer_repository import TxIndexerRepository
from app.services.tx_indexer_input import (
    MAX_ACCOUNT_LENGTH,
    MAX_ASSET_CODE_LENGTH,
    MAX_HASH_LENGTH,
    MAX_TOKEN_LENGTH,
    TxIndexerValidationError,
    TxIndexFilters,
    sanitize_transaction_hash,
)
from app.utils.cursor import decode_cursor, encode_cursor

router = APIRouter()

#: Default and maximum page size for the read API. The cap mirrors Horizon's
#: own page limit so a client cannot ask the index for more than the indexer
#: would ever write in one ingest pass.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class IndexedTransactionResponse(BaseModel):
    """An on-chain transaction as stored by the indexer."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "idx_9c1f4f2f0f5b4a1d8c3e2b7a6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c",
                "transaction_hash": "9c1f4f2f0f5b4a1d8c3e2b7a6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c",
                "type": "payment",
                "amount": 150000000,
                "asset_code": "USDC",
                "from_address": "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF",
                "to_address": "GBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBXY",
                "status": "confirmed",
                "created_at": "2026-10-20T12:00:00Z",
                "confirmed_at": "2026-10-20T12:00:05Z",
            }
        },
    )

    id: str = Field(description="Index row id, derived from the transaction hash (``idx_<hash>``).")
    transaction_hash: str = Field(description="Stellar transaction hash. Unique in the index.")
    type: str = Field(description="Horizon operation type that was indexed, e.g. ``payment``.")
    amount: int = Field(description="Amount in stroops. 1 unit = 10^7 stroops.")
    asset_code: str = Field(description="Asset code; ``XLM`` for native payments.")
    from_address: str = Field(description="Source Stellar account of the payment.")
    to_address: str = Field(description="Destination Stellar account of the payment.")
    status: str = Field(description="Ledger status of the indexed row; always ``confirmed``.")
    created_at: datetime = Field(description="Ledger close time reported by Horizon (UTC).")
    confirmed_at: datetime | None = Field(
        default=None,
        description="When the indexer stored the row. Not the on-chain confirmation time.",
    )


class IndexedTransactionPage(BaseModel):
    """One page of the transaction index."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "id": "idx_9c1f4f2f0f5b4a1d8c3e2b7a6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c",
                        "transaction_hash": "9c1f4f2f0f5b4a1d8c3e2b7a6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c",
                        "type": "payment",
                        "amount": 150000000,
                        "asset_code": "USDC",
                        "from_address": "GAAAA...",
                        "to_address": "GBBBB...",
                        "status": "confirmed",
                        "created_at": "2026-10-20T12:00:00Z",
                        "confirmed_at": "2026-10-20T12:00:05Z",
                    }
                ],
                "next_cursor": "eyJpZCI6ICJpZHhfLi4uIiwgInYiOiAiMjAyNi0xMC0yMFQxMjowMDowMCswMDowMCJ9",
                "has_more": True,
            }
        }
    )

    items: list[IndexedTransactionResponse] = Field(description="Rows on this page, newest first.")
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque cursor for the next page. Pass it back as ``cursor``. Null when this is " "the last page."
        ),
    )
    has_more: bool = Field(description="True when at least one more row matches the filters.")


def _to_response(row: object) -> IndexedTransactionResponse:
    """Validate one ORM row against the response model."""
    return IndexedTransactionResponse.model_validate(row)


def _decode_cursor_param(cursor: str | None) -> tuple[datetime, str] | None:
    """Decode the opaque ``cursor`` query parameter into a keyset position.

    A cursor that cannot be decoded is a 422 rather than a silent first page:
    restarting from the top makes a client loop over the same rows forever
    while believing it is paging forward.
    """
    if cursor is None:
        return None

    decoded = decode_cursor(cursor)
    if decoded is None:
        raise CodedHTTPException(status_code=422, detail="Invalid cursor")

    row_id, sort_value = decoded
    try:
        created_at = datetime.fromisoformat(str(sort_value))
    except (TypeError, ValueError) as exc:
        raise CodedHTTPException(status_code=422, detail="Invalid cursor") from exc

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at, str(row_id)


def _build_page(rows: Sequence[PaymentTransactionORM], limit: int) -> IndexedTransactionPage:
    """Trim a ``limit + 1`` result set into a page plus its continuation cursor."""
    has_more = len(rows) > limit
    page_rows = list(rows[:limit])

    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last.id, last.created_at.isoformat())

    return IndexedTransactionPage(
        items=[_to_response(row) for row in page_rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


def _validated_filters(**raw: object) -> TxIndexFilters:
    """Sanitize query values, mapping the first invalid one to a 422.

    Validation happens before the repository is touched, so a malformed filter
    never reaches SQL and never costs a query.
    """
    try:
        filters = TxIndexFilters.from_query(**raw)  # type: ignore[arg-type]
    except TxIndexerValidationError as exc:
        raise CodedHTTPException(status_code=422, detail=str(exc)) from exc

    if filters.created_after and filters.created_before and filters.created_after > filters.created_before:
        raise CodedHTTPException(status_code=422, detail="created_after cannot be after created_before")
    return filters


@router.get(
    "/transactions",
    response_model=IndexedTransactionPage,
    summary="List indexed Stellar transactions",
    description=(
        "Return the transactions the indexer has stored, newest first. All filters are optional "
        "and combine with AND.\n\n"
        "Paging is cursor based: send ``limit`` (1-200, default 50) and pass the returned "
        "``next_cursor`` back as ``cursor`` until ``has_more`` is false. The cursor encodes the "
        "``(created_at, id)`` of the last row, so rows inserted while a client pages do not "
        "shift or duplicate results. ``offset`` is still accepted for callers that cannot hold "
        "a cursor; when ``cursor`` is present it takes precedence and ``offset`` is ignored.\n\n"
        "Requires an engineer or admin bearer token."
    ),
    response_description="One page of matching indexed transactions, plus the cursor for the next page.",
    operation_id="listIndexedTransactions",
    responses={
        200: {"description": "One page of matching indexed transactions."},
        401: {"description": "Missing or invalid bearer token."},
        403: {"description": "The caller's role cannot read the transaction index."},
        422: {
            "description": (
                "A query parameter failed validation (for example ``limit=0``), the date window is "
                "reversed, or ``cursor`` is not a cursor issued by this endpoint."
            )
        },
    },
)
def list_indexed_transactions(
    transaction_hash: str | None = Query(
        default=None,
        max_length=MAX_HASH_LENGTH,
        description=(
            "Exact Stellar transaction hash to look up: 64 hexadecimal characters "
            "(case-insensitive; normalized to lower case)."
        ),
    ),
    account: str | None = Query(
        default=None,
        max_length=MAX_ACCOUNT_LENGTH,
        description="Stellar account; matches either ``from_address`` or ``to_address``.",
    ),
    asset_code: str | None = Query(
        default=None,
        max_length=MAX_ASSET_CODE_LENGTH,
        description="Asset code filter, for example ``USDC`` or ``XLM`` (normalized to upper case).",
    ),
    status: str | None = Query(
        default=None,
        max_length=MAX_TOKEN_LENGTH,
        description="Ledger status filter. Indexed rows are always ``confirmed``.",
    ),
    type: str | None = Query(
        default=None,
        max_length=MAX_TOKEN_LENGTH,
        description="Horizon operation type filter, for example ``payment``.",
    ),
    created_after: datetime | None = Query(
        default=None,
        description="Only rows whose ledger close time is at or after this UTC timestamp.",
    ),
    created_before: datetime | None = Query(
        default=None,
        description="Only rows whose ledger close time is at or before this UTC timestamp.",
    ),
    limit: int = Query(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Maximum number of rows to return (1-{MAX_PAGE_SIZE}).",
    ),
    cursor: str | None = Query(
        default=None,
        description=("Opaque cursor from a previous page's ``next_cursor``. Takes precedence over ``offset``."),
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Rows to skip when paging by offset. Ignored when ``cursor`` is supplied.",
    ),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
) -> IndexedTransactionPage:
    """List indexed transactions with filtering and cursor-first pagination."""
    filters = _validated_filters(
        transaction_hash=transaction_hash,
        account=account,
        asset_code=asset_code,
        status=status,
        transaction_type=type,
        created_after=created_after,
        created_before=created_before,
    )
    after = _decode_cursor_param(cursor)

    rows = TxIndexerRepository(db).list_indexed(
        **asdict(filters),
        limit=limit + 1,
        offset=0 if after else offset,
        after=after,
    )
    return _build_page(rows, limit)


@router.get(
    "/transactions/{transaction_hash}",
    response_model=IndexedTransactionResponse,
    summary="Get one indexed transaction",
    description=(
        "Return a single indexed transaction by its Stellar transaction hash. Returns 404 when "
        "the indexer has not stored that hash (either it is not a payment, or the ingest cursor "
        "has not reached it yet)."
    ),
    response_description="The indexed transaction.",
    operation_id="getIndexedTransaction",
    responses={
        200: {"description": "The indexed transaction."},
        401: {"description": "Missing or invalid bearer token."},
        403: {"description": "The caller's role cannot read the transaction index."},
        404: {"description": "No indexed transaction exists for that hash."},
        422: {"description": "The hash is not a 64-character hexadecimal Stellar transaction hash."},
    },
)
def get_indexed_transaction(
    transaction_hash: str,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
) -> IndexedTransactionResponse:
    """Return one indexed transaction by hash."""
    try:
        canonical_hash = sanitize_transaction_hash(transaction_hash)
    except TxIndexerValidationError as exc:
        raise CodedHTTPException(status_code=422, detail=str(exc)) from exc

    assert canonical_hash is not None  # a path parameter is always present
    row = TxIndexerRepository(db).get_by_hash(canonical_hash)
    if row is None:
        raise CodedHTTPException(status_code=404, detail="Indexed transaction not found")
    return _to_response(row)
