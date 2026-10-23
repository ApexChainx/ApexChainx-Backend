"""Wallet API endpoints (refactored for Postgres-backed persistence — issue #49).

All handlers now pass a request-scoped database session to WalletRegistry.
"""

import logging

from fastapi import APIRouter, Depends, status
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
    WalletLinkRequest,
    WalletStatusResponse,
    WalletTrustlineResponse,
)
from app.services.wallet_registry import WalletRegistry

router = APIRouter()

logger = logging.getLogger(__name__)


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
