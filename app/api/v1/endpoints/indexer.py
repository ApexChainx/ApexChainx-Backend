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

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.coded_errors import CodedHTTPException
from app.core.security import require_engineer
from app.db.session import get_db
from app.repositories.tx_indexer_repository import TxIndexerRepository

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


def _to_response(row: object) -> IndexedTransactionResponse:
    """Validate one ORM row against the response model."""
    return IndexedTransactionResponse.model_validate(row)


@router.get(
    "/transactions",
    response_model=list[IndexedTransactionResponse],
    summary="List indexed Stellar transactions",
    description=(
        "Return the transactions the indexer has stored, newest first. All filters are optional "
        "and combine with AND. Results are paged with ``limit``/``offset``; the maximum page size "
        "is 200 rows. Requires an engineer or admin bearer token."
    ),
    response_description="Matching indexed transactions, ordered by ledger close time descending.",
    operation_id="listIndexedTransactions",
    responses={
        200: {"description": "Matching indexed transactions."},
        401: {"description": "Missing or invalid bearer token."},
        403: {"description": "The caller's role cannot read the transaction index."},
        422: {"description": "A query parameter failed validation (for example ``limit=0``)."},
    },
)
def list_indexed_transactions(
    transaction_hash: str | None = Query(
        default=None,
        max_length=255,
        description="Exact Stellar transaction hash to look up.",
    ),
    account: str | None = Query(
        default=None,
        max_length=255,
        description="Stellar account; matches either ``from_address`` or ``to_address``.",
    ),
    asset_code: str | None = Query(
        default=None,
        max_length=20,
        description="Asset code filter, for example ``USDC`` or ``XLM``.",
    ),
    status: str | None = Query(
        default=None,
        max_length=50,
        description="Ledger status filter. Indexed rows are always ``confirmed``.",
    ),
    type: str | None = Query(
        default=None,
        max_length=50,
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
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of matching rows to skip before the page.",
    ),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
) -> list[IndexedTransactionResponse]:
    """List indexed transactions with filtering and offset pagination."""
    if created_after and created_before and created_after > created_before:
        raise CodedHTTPException(
            status_code=422,
            detail="created_after cannot be after created_before",
        )

    rows = TxIndexerRepository(db).list_indexed(
        transaction_hash=transaction_hash,
        account=account,
        asset_code=asset_code,
        status=status,
        transaction_type=type,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )
    return [_to_response(row) for row in rows]


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
    },
)
def get_indexed_transaction(
    transaction_hash: str,
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
) -> IndexedTransactionResponse:
    """Return one indexed transaction by hash."""
    row = TxIndexerRepository(db).get_by_hash(transaction_hash)
    if row is None:
        raise CodedHTTPException(status_code=404, detail="Indexed transaction not found")
    return _to_response(row)
