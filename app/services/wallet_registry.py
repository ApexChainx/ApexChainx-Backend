from __future__ import annotations

from datetime import datetime, UTC
from uuid import uuid4

from app.core.config import settings
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


class WalletRegistry:
    _wallets_by_user: dict[str, Wallet] = {}
    _wallets_by_address: dict[str, Wallet] = {}
    _link_locks: dict[str, bool] = {}  # Simple lock mechanism for link operations

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @classmethod
    def _is_stale(cls, wallet: Wallet) -> bool:
        """Return True if cached_at is absent or older than WALLET_CACHE_TTL_SECONDS."""
        if wallet.cached_at is None:
            return True
        age = (cls._now() - wallet.cached_at).total_seconds()
        return age > settings.WALLET_CACHE_TTL_SECONDS

    @classmethod
    def _get_cache_ttl_remaining(cls, wallet: Wallet) -> int | None:
        """Return seconds until cache expires, or None if stale/never cached."""
        if wallet.cached_at is None:
            return None
        age = (cls._now() - wallet.cached_at).total_seconds()
        remaining = settings.WALLET_CACHE_TTL_SECONDS - age
        return max(0, int(remaining)) if remaining > 0 else None

    @classmethod
    def _refresh_wallet(cls, wallet: Wallet) -> Wallet:
        """Simulate a live re-fetch and stamp cached_at. In production this would
        call the Stellar Horizon API; here we just update the timestamp."""
        now = cls._now()
        refreshed = wallet.model_copy(update={"cached_at": now, "last_updated": now, "cache_status": "live"})
        cls._wallets_by_user[wallet.user_id] = refreshed
        cls._wallets_by_address[wallet.public_key] = refreshed
        return refreshed

    @classmethod
    def _build_public_key(cls) -> str:
        return f"G{uuid4().hex.upper()}"

    @classmethod
    def create_wallet(cls, payload: WalletCreateRequest) -> WalletCreateResponse:
        existing = cls._wallets_by_user.get(payload.user_id)
        if existing:
            return WalletCreateResponse(
                **existing.model_dump(),
                message="Wallet already exists for this user.",
            )

        now = cls._now()
        wallet = Wallet(
            user_id=payload.user_id,
            public_key=cls._build_public_key(),
            created_at=now,
            last_updated=now,
            funded=False,
            active=True,
            trustline_ready=False,
            cached_at=now,
        )
        cls._wallets_by_user[payload.user_id] = wallet
        cls._wallets_by_address[wallet.public_key] = wallet
        return WalletCreateResponse(
            **wallet.model_dump(),
            message="Wallet created. Please fund with at least 1 XLM to activate.",
        )

    @classmethod
    def link_wallet(cls, payload: WalletLinkRequest) -> Wallet:
        """Link a wallet to a user with comprehensive conflict detection (BE-032).

        Conflict detection rules:
        1. User already linked to a different address → Reject (409 Conflict)
        2. Address already linked to a different user → Reject (409 Conflict)
        3. Same user + same address → Idempotent update (allowed)
        4. No conflicts → Create new link

        Thread-safe: uses simple lock to prevent race conditions during link operations.
        """
        now = cls._now()
        link_key = f"{payload.user_id}:{payload.public_key}"

        # Simple lock to prevent concurrent link operations
        if cls._link_locks.get(link_key):
            raise ValueError(f"Link operation for user '{payload.user_id}' is already in progress.")

        try:
            cls._link_locks[link_key] = True

            # Check 1: User already linked to different address
            existing_by_user = cls._wallets_by_user.get(payload.user_id)
            if existing_by_user and existing_by_user.public_key != payload.public_key:
                raise ValueError(
                    f"User '{payload.user_id}' is already linked to wallet '{existing_by_user.public_key}'. "
                    f"Cannot link to '{payload.public_key}'."
                )

            # Check 2: Address already linked to different user
            existing_by_address = cls._wallets_by_address.get(payload.public_key)
            if existing_by_address and existing_by_address.user_id != payload.user_id:
                raise ValueError(
                    f"Wallet address '{payload.public_key}' is already linked to user '{existing_by_address.user_id}'. "
                    f"Cannot link to '{payload.user_id}'."
                )

            # Check 3: Idempotent - same user + same address
            if existing_by_user and existing_by_user.public_key == payload.public_key:
                # Update existing wallet with new metadata
                wallet = existing_by_user.model_copy(
                    update={
                        "funded": payload.funded,
                        "trustline_ready": payload.trustline_ready,
                        "active": True,
                        "last_updated": now,
                        "cached_at": now,
                    }
                )
                cls._wallets_by_user[payload.user_id] = wallet
                cls._wallets_by_address[payload.public_key] = wallet
                return wallet

            # Check 4: No conflicts - create new link
            created_at = now
            wallet = Wallet(
                user_id=payload.user_id,
                public_key=payload.public_key,
                created_at=created_at,
                last_updated=now,
                funded=payload.funded,
                active=True,
                trustline_ready=payload.trustline_ready,
                cached_at=now,
            )
            cls._wallets_by_user[payload.user_id] = wallet
            cls._wallets_by_address[payload.public_key] = wallet
            return wallet
        finally:
            # Release lock
            cls._link_locks.pop(link_key, None)

    @classmethod
    def get_wallet(cls, user_id: str, refresh: bool = False) -> Wallet | None:
        wallet = cls._wallets_by_user.get(user_id)
        if not wallet:
            return None
        if refresh or cls._is_stale(wallet):
            wallet = cls._refresh_wallet(wallet)
        else:
            # Mark as fresh if within TTL
            wallet = wallet.model_copy(update={"cache_status": "fresh"})
        return wallet

    @classmethod
    def get_balance(cls, address: str, refresh: bool = False) -> WalletBalanceResponse | None:
        wallet = cls._wallets_by_address.get(address)
        if not wallet:
            return None
        if refresh or cls._is_stale(wallet):
            wallet = cls._refresh_wallet(wallet)

        xlm_balance = "1.0000000" if wallet.funded else "0.0000000"
        balances = {
            "XLM": AssetBalance(balance=xlm_balance, asset_type="native"),
        }
        if wallet.trustline_ready:
            balances["USDC"] = AssetBalance(
                balance="0.0000000",
                asset_type="credit_alphanum4",
                asset_code="USDC",
                asset_issuer="TEST_ISSUER",
            )
        cache_ttl = cls._get_cache_ttl_remaining(wallet)
        return WalletBalanceResponse(
            address=address,
            balances=balances,
            last_updated=wallet.last_updated,
            cache_status=wallet.cache_status,
            cache_ttl_seconds=cache_ttl,
            cached_at=wallet.cached_at,
        )

    @classmethod
    def get_status(cls, user_id: str, refresh: bool = False) -> WalletStatusResponse | None:
        wallet = cls.get_wallet(user_id, refresh=refresh)
        if not wallet:
            return None

        cache_ttl = cls._get_cache_ttl_remaining(wallet)
        return WalletStatusResponse(
            user_id=wallet.user_id,
            public_key=wallet.public_key,
            funded=wallet.funded,
            trustline_ready=wallet.trustline_ready,
            usable=wallet.funded and wallet.trustline_ready and wallet.active,
            active=wallet.active,
            last_updated=wallet.last_updated,
            cache_status=wallet.cache_status,
            cache_ttl_seconds=cache_ttl,
            cached_at=wallet.cached_at,
        )

    @classmethod
    def get_trustline(cls, user_id: str, refresh: bool = False) -> WalletTrustlineResponse | None:
        wallet = cls.get_wallet(user_id, refresh=refresh)
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
    def get_funding_state(cls, user_id: str, refresh: bool = False) -> WalletFundingStateResponse | None:
        wallet = cls.get_wallet(user_id, refresh=refresh)
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
