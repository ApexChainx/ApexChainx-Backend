"""Wallet API endpoints (refactored for Postgres-backed persistence — issue #49).

All handlers now pass a request-scoped database session to WalletRegistry.

Issue #698 — Health check endpoint:
    GET /api/v1/wallets/health  reports whether the wallet table is reachable
    and returns a structured health report, consistent with the global
    /health/readiness pattern.

Issue #701 — Pagination:
    GET /api/v1/wallets  returns a paginated list of all wallets, accepting
    ?page and ?page_size query parameters and an optional ?include_total flag.
"""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import ApexConflictError
from app.core.security import require_engineer
from app.db.session import get_db
from app.models.wallet import (
    Wallet,
    WalletBalanceResponse,
    WalletCreateRequest,
    WalletCreateResponse,
    WalletFundingStateResponse,
    WalletHealthComponent,
    WalletHealthResponse,
    WalletLinkRequest,
    WalletListResponse,
    WalletStatusResponse,
    WalletTrustlineResponse,
)
from app.services.wallet_registry import WalletRegistry

router = APIRouter()


# ── Health check (#698) ────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=WalletHealthResponse,
    summary="WalletAPI health check",
    tags=["wallets"],
)
def wallet_health(db: Session = Depends(get_db)) -> WalletHealthResponse:
    """Report the health of the WalletAPI service (issue #698).

    Checks wallet table accessibility with a lightweight SELECT 1 probe.
    Returns:
    - 200 with status "ok"   — wallet table is reachable
    - 200 with status "warn" — degraded but partially functional
    - 503 with status "down" — wallet table is unreachable
    """
    start = time.monotonic()
    try:
        db.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        db_component = WalletHealthComponent(
            status="ok",
            details={"latency_ms": latency_ms},
        )
    except SQLAlchemyError as exc:
        db_component = WalletHealthComponent(
            status="down",
            details={"error": str(exc)},
        )

    overall = db_component.status
    response = WalletHealthResponse(
        status=overall,
        components={"wallet_database": db_component},
        timestamp=datetime.now(UTC).isoformat(),
    )

    if overall == "down":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.model_dump(),
        )
    return response


# ── Existing endpoints ─────────────────────────────────────────────────────────


@router.post("/create", response_model=WalletCreateResponse, status_code=status.HTTP_201_CREATED)
def create_wallet(
    payload: WalletCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> WalletCreateResponse:
    """Create the user's wallet.

    Raises ``ApexWalletAlreadyExistsError`` (409) when the user already has one,
    including when a concurrent request wins the race for the unique constraint
    (issue #531). One user gets one wallet, and a retried registration is told
    which address already exists instead of creating a second row.
    """
    return WalletRegistry.create_wallet(db, payload)


@router.post("/link", response_model=Wallet)
def link_wallet(
    payload: WalletLinkRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> Wallet:
    try:
        return WalletRegistry.link_wallet(db, payload)
    except ApexConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/ping")
def wallets_ping() -> dict[str, str]:
    return {"message": "wallets ok"}


# ── Pagination (#701) ──────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=WalletListResponse,
    summary="List wallets (paginated)",
    tags=["wallets"],
)
def list_wallets(
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page (max 100)"),
    include_total: bool = Query(default=True, description="Include total count (uses COUNT(*) OVER())"),
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> WalletListResponse:
    """Return a paginated list of wallets (issue #701).

    Uses a single-pass COUNT(*) OVER() window function to return the total
    count alongside the page items, avoiding a second round-trip to the DB.
    Pass ``?include_total=false`` to skip the count when not needed.
    """
    return WalletRegistry.list_wallets(db, page=page, page_size=page_size, include_total=include_total)


# ── Per-wallet endpoints ───────────────────────────────────────────────────────


@router.get("/{user_id}", response_model=Wallet)
def get_wallet(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> Wallet:
    wallet = WalletRegistry.get_wallet(db, user_id)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return wallet


@router.get("/{user_id}/status", response_model=WalletStatusResponse)
def get_wallet_status(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> WalletStatusResponse:
    wallet_status = WalletRegistry.get_status(db, user_id)
    if not wallet_status:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return wallet_status


@router.get(
    "/{user_id}/trustline", response_model=WalletTrustlineResponse, summary="Check trustline readiness for a wallet"
)
def get_wallet_trustline(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> WalletTrustlineResponse:
    result = WalletRegistry.get_trustline(db, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return result


@router.get(
    "/{user_id}/funding-state",
    response_model=WalletFundingStateResponse,
    summary="Get current funding state of a wallet",
)
def get_wallet_funding_state(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> WalletFundingStateResponse:
    result = WalletRegistry.get_funding_state(db, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return result


@router.get("/address/{address}/balance", response_model=WalletBalanceResponse)
def get_wallet_balance(
    address: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> WalletBalanceResponse:
    balance = WalletRegistry.get_balance(db, address)
    if not balance:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return balance
