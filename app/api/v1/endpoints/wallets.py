from fastapi import APIRouter, HTTPException, Query, status, Depends

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
from app.core.security import require_engineer

router = APIRouter()


@router.post("/create", response_model=WalletCreateResponse, status_code=status.HTTP_201_CREATED)
def create_wallet(payload: WalletCreateRequest, current_user=Depends(require_engineer)):
    return WalletRegistry.create_wallet(payload)


@router.post("/link", response_model=Wallet)
def link_wallet(payload: WalletLinkRequest, current_user=Depends(require_engineer)):
    try:
        return WalletRegistry.link_wallet(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/ping")
def wallets_ping():
    return {"message": "wallets ok"}


@router.get("/{user_id}", response_model=Wallet)
def get_wallet(
    user_id: str,
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    wallet = WalletRegistry.get_wallet(user_id, refresh=refresh)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return wallet


@router.get("/{user_id}/status", response_model=WalletStatusResponse)
def get_wallet_status(
    user_id: str,
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    wallet_status = WalletRegistry.get_status(user_id, refresh=refresh)
    if not wallet_status:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return wallet_status


@router.get(
    "/{user_id}/trustline", response_model=WalletTrustlineResponse, summary="Check trustline readiness for a wallet"
)
def get_wallet_trustline(
    user_id: str,
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    result = WalletRegistry.get_trustline(user_id, refresh=refresh)
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
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    result = WalletRegistry.get_funding_state(user_id, refresh=refresh)
    if not result:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return result


@router.get("/{address}/balance", response_model=WalletBalanceResponse)
def get_wallet_balance(
    address: str,
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    balance = WalletRegistry.get_balance(address, refresh=refresh)
    if not balance:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return balance
