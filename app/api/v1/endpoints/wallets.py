"""Wallet API endpoints (refactored for Postgres-backed persistence — issue #49).

All handlers now pass a request-scoped database session to WalletRegistry.
"""

from fastapi import APIRouter, Depends, HTTPException, status
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
    WalletLinkRequest,
    WalletStatusResponse,
    WalletTrustlineResponse,
)
from app.services.wallet_registry import WalletRegistry

router = APIRouter()


@router.post("/create", response_model=WalletCreateResponse, status_code=status.HTTP_201_CREATED)
def create_wallet(
    payload: WalletCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_engineer),
) -> WalletCreateResponse:
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
