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

from app.api.coded_errors import CodedHTTPException
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

logger = logging.getLogger(__name__)


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
    try:
        wallet = WalletRegistry.create_wallet(db, payload)
    except ApexConflictError:
        # Preserve the registry's intended 409 (wallet_already_exists).
        raise
    except Exception as exc:
        logger.error("wallet_create_failed", extra={"event": "wallet_create_failed", "user_id": payload.user_id})
        raise CodedHTTPException(status_code=500, detail="Failed to create wallet") from exc
    logger.info("wallet_created", extra={"event": "wallet_created", "user_id": payload.user_id})
    return wallet


@router.post("/link", response_model=Wallet)
def link_wallet(
    payload: WalletLinkRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> Wallet:
    try:
        wallet = WalletRegistry.link_wallet(db, payload)
    except ApexConflictError as exc:
        # wallet_already_linked (#569): the registry rejects linking a user or
        # address that is already bound to a different entity.
        logger.warning("wallet_link_conflict", extra={"event": "wallet_link_conflict", "user_id": payload.user_id})
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc), error_code="wallet_already_linked"
        )
    except Exception as exc:
        logger.error("wallet_link_failed", extra={"event": "wallet_link_failed", "user_id": payload.user_id})
        raise CodedHTTPException(status_code=500, detail="Failed to link wallet") from exc
    logger.info("wallet_linked", extra={"event": "wallet_linked", "user_id": payload.user_id})
    return wallet


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
    try:
        wallet = WalletRegistry.get_wallet(db, user_id)
    except CodedHTTPException:
        raise
    except Exception as exc:
        logger.error("wallet_lookup_failed", extra={"event": "wallet_lookup_failed", "user_id": user_id})
        raise CodedHTTPException(status_code=500, detail="Failed to fetch wallet") from exc
    if not wallet:
        logger.warning("wallet_not_found", extra={"event": "wallet_not_found", "user_id": user_id})
        raise CodedHTTPException(status_code=404, detail="Wallet not found", error_code="wallet_not_found")
    return wallet


@router.get("/{user_id}/status", response_model=WalletStatusResponse)
def get_wallet_status(
    user_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> WalletStatusResponse:
    wallet_status = WalletRegistry.get_status(db, user_id)
    if not wallet_status:
        raise CodedHTTPException(status_code=404, detail="Wallet not found", error_code="wallet_not_found")
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
        raise CodedHTTPException(status_code=404, detail="Wallet not found", error_code="wallet_not_found")
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
        raise CodedHTTPException(status_code=404, detail="Wallet not found", error_code="wallet_not_found")
    return result


@router.get("/address/{address}/balance", response_model=WalletBalanceResponse)
def get_wallet_balance(
    address: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> WalletBalanceResponse:
    balance = WalletRegistry.get_balance(db, address)
    if not balance:
        raise CodedHTTPException(status_code=404, detail="Wallet not found", error_code="wallet_not_found")
    return balance
