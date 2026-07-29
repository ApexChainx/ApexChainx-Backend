"""Wallet registry service with Postgres-backed persistence (issue #49).

Delegates durable storage to WalletRepository and wraps reads with an optional
Redis-backed read-through cache (WalletCache).  The in-memory dicts have been
removed so that wallet state survives restarts and works across multi-worker
deployments.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ApexConflictError
from app.models.wallet import (
    AssetBalance,
    Wallet,
    WalletBalanceResponse,
    WalletCreateRequest,
    WalletCreateResponse,
    WalletFundingStateResponse,
    WalletLinkRequest,
    WalletStatusResponse,
    WalletTrustlineResponse,
)
from app.models.orm.wallet import WalletORM
from app.repositories.wallet_repository import WalletRepository
from app.services.wallet_cache import WalletCache


def _orm_to_pydantic(w: WalletORM) -> Wallet:
    """Convert ORM row to the Pydantic Wallet model."""
    return Wallet(
        user_id=w.user_id,
        public_key=w.public_key,
        created_at=w.created_at,
        last_updated=w.last_updated,
        funded=w.funded,
        active=w.active,
        trustline_ready=w.trustline_ready,
        cached_at=w.cached_at,
        cache_status="fresh",
    )


class WalletRegistry:
    """Wallet lifecycle service backed by Postgres + optional Redis cache.

    Public methods accept a ``db: Session`` parameter so callers (e.g. FastAPI
    route handlers) can inject the request-scoped session.  The ``cache``
    parameter is optional; when provided it acts as a read-through cache.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _build_public_key() -> str:
        return f"G{uuid4().hex.upper()}"

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @classmethod
    def create_wallet(
        cls,
        db: Session,
        payload: WalletCreateRequest,
        cache: Optional[WalletCache] = None,
    ) -> WalletCreateResponse:
        repo = WalletRepository(db)

        existing = repo.get_by_user_id(payload.user_id)
        if existing:
            wallet = _orm_to_pydantic(existing)
            return WalletCreateResponse(
                **wallet.model_dump(),
                message="Wallet already exists for this user.",
            )

        public_key = cls._build_public_key()
        orm = repo.create(user_id=payload.user_id, public_key=public_key)

        wallet = _orm_to_pydantic(orm)
        if cache:
            cache.set(public_key, wallet.model_dump(mode="json"))

        return WalletCreateResponse(
            **wallet.model_dump(),
            message="Wallet created. Please fund with at least 1 XLM to activate.",
        )

    # ------------------------------------------------------------------
    # Link
    # ------------------------------------------------------------------

    @classmethod
    def link_wallet(
        cls,
        db: Session,
        payload: WalletLinkRequest,
        cache: Optional[WalletCache] = None,
    ) -> Wallet:
        """Link a wallet to a user with comprehensive conflict detection (BE-032).

        Conflict detection rules:
        1. User already linked to a different address → 409
        2. Address already linked to a different user → 409
        3. Same user + same address → Idempotent update (allowed)
        4. No conflicts → Create new link
        """
        repo = WalletRepository(db)

        existing_by_user = repo.get_by_user_id(payload.user_id)
        existing_by_address = repo.get_by_public_key(payload.public_key)

        # Check 1: User already linked to different address
        if existing_by_user and existing_by_user.public_key != payload.public_key:
            raise ApexConflictError(
                detail=f"User '{payload.user_id}' is already linked to wallet '{existing_by_user.public_key}'. "
                f"Cannot link to '{payload.public_key}'.",
                fields={"user_id": "already linked to a different wallet"},
            )

        # Check 2: Address already linked to different user
        if existing_by_address and existing_by_address.user_id != payload.user_id:
            raise ApexConflictError(
                detail=f"Wallet address '{payload.public_key}' is already linked to user "
                f"'{existing_by_address.user_id}'. Cannot link to '{payload.user_id}'.",
                fields={"public_key": "already linked to a different user"},
            )

        # Check 3: Idempotent - same user + same address
        if existing_by_user and existing_by_user.public_key == payload.public_key:
            orm = repo.update(
                existing_by_user,
                funded=payload.funded,
                trustline_ready=payload.trustline_ready,
            )
            wallet = _orm_to_pydantic(orm)
            if cache:
                cache.set(wallet.public_key, wallet.model_dump(mode="json"))
            return wallet

        # Check 4: No conflicts - create new link
        orm = repo.create(
            user_id=payload.user_id,
            public_key=payload.public_key,
            funded=payload.funded,
            trustline_ready=payload.trustline_ready,
        )
        wallet = _orm_to_pydantic(orm)
        if cache:
            cache.set(wallet.public_key, wallet.model_dump(mode="json"))
        return wallet

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    @classmethod
    def _fetch_wallet(
        cls,
        db: Session,
        user_id: str,
        cache: Optional[WalletCache] = None,
    ) -> Wallet | None:
        """Internal helper: fetch from cache or DB, update cache on miss."""
        repo = WalletRepository(db)
        orm = repo.get_by_user_id(user_id)
        if orm is None:
            return None

        wallet = _orm_to_pydantic(orm)
        # Touch the cache timestamp in DB to simulate freshness tracking
        repo.touch_cache(orm)
        if cache:
            cache.set(wallet.public_key, wallet.model_dump(mode="json"))
        return wallet

    @classmethod
    def _fetch_by_address(
        cls,
        db: Session,
        address: str,
        cache: Optional[WalletCache] = None,
    ) -> Wallet | None:
        """Internal helper: fetch by public key from cache or DB."""
        if cache:
            cached = cache.get(address)
            if cached:
                return Wallet(**cached)

        repo = WalletRepository(db)
        orm = repo.get_by_public_key(address)
        if orm is None:
            return None

        wallet = _orm_to_pydantic(orm)
        if cache:
            cache.set(address, wallet.model_dump(mode="json"))
        return wallet

    # ------------------------------------------------------------------
    # Public read methods
    # ------------------------------------------------------------------

    @classmethod
    def get_wallet(
        cls,
        db: Session,
        user_id: str,
        cache: Optional[WalletCache] = None,
    ) -> Wallet | None:
        return cls._fetch_wallet(db, user_id, cache=cache)

    @classmethod
    def get_balance(
        cls,
        db: Session,
        address: str,
        cache: Optional[WalletCache] = None,
    ) -> WalletBalanceResponse | None:
        wallet = cls._fetch_by_address(db, address, cache=cache)
        if not wallet:
            return None

        xlm_balance = "1.0000000" if wallet.funded else "0.0000000"
        balances: dict[str, AssetBalance] = {
            "XLM": AssetBalance(balance=xlm_balance, asset_type="native"),
        }
        if wallet.trustline_ready:
            balances["USDC"] = AssetBalance(
                balance="0.0000000",
                asset_type="credit_alphanum4",
                asset_code="USDC",
                asset_issuer="TEST_ISSUER",
            )
        return WalletBalanceResponse(
            address=address,
            balances=balances,
            last_updated=wallet.last_updated,
            cache_status=wallet.cache_status,
            cache_ttl_seconds=None,
            cached_at=wallet.cached_at,
        )

    @classmethod
    def get_status(
        cls,
        db: Session,
        user_id: str,
        cache: Optional[WalletCache] = None,
    ) -> WalletStatusResponse | None:
        wallet = cls.get_wallet(db, user_id, cache=cache)
        if not wallet:
            return None
        return WalletStatusResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            funded=wallet.funded,
            trustline_ready=wallet.trustline_ready,
            usable=wallet.funded and wallet.trustline_ready and wallet.active,
            active=wallet.active,
            last_updated=wallet.last_updated,
            cache_status=wallet.cache_status,
            cache_ttl_seconds=None,
            cached_at=wallet.cached_at,
        )

    @classmethod
    def get_trustline(
        cls,
        db: Session,
        user_id: str,
        cache: Optional[WalletCache] = None,
    ) -> WalletTrustlineResponse | None:
        wallet = cls.get_wallet(db, user_id, cache=cache)
        if not wallet:
            return None
        error = None if wallet.trustline_ready else "Trustline not established. Fund wallet and set up USDC trustline."
        return WalletTrustlineResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            trustline_ready=wallet.trustline_ready,
            trustline_error=error,
            cache_status=wallet.cache_status,
            cached_at=wallet.cached_at,
        )

    @classmethod
    def get_funding_state(
        cls,
        db: Session,
        user_id: str,
        cache: Optional[WalletCache] = None,
    ) -> WalletFundingStateResponse | None:
        wallet = cls.get_wallet(db, user_id, cache=cache)
        if not wallet:
            return None
        error = None if wallet.funded else "Wallet is not funded. Send at least 1 XLM to activate."
        return WalletFundingStateResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            funded=wallet.funded,
            funding_error=error,
            cache_status=wallet.cache_status,
            cached_at=wallet.cached_at,
        )
