"""Postgres-backed wallet persistence layer (issue #49).

Provides durable wallet identity with uniqueness enforcement:
- user_id is unique → concurrent register → 409
- public_key is unique → prevents double-linking
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Optional

from sqlalchemy.orm import Session

from app.models.orm.wallet import WalletORM


class WalletRepository:
    """ORM-backed repository for wallet persistence.

    Replaces the in-memory dict store in WalletRegistry with Postgres-backed
    storage so wallet state survives restarts and works across multiple workers.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Create ────────────────────────────────────────────────────────────

    def create(
        self,
        user_id: str,
        public_key: str,
        funded: bool = False,
        trustline_ready: bool = False,
    ) -> WalletORM:
        """Persist a new wallet.  Raises ValueError on duplicate user_id or public_key."""
        now = datetime.now(UTC)
        wallet = WalletORM(
            user_id=user_id,
            public_key=public_key,
            funded=funded,
            active=True,
            trustline_ready=trustline_ready,
            created_at=now,
            last_updated=now,
            cached_at=now,
        )
        self.db.add(wallet)
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_user_id(self, user_id: str) -> Optional[WalletORM]:
        """Look up a wallet by user_id."""
        return self.db.query(WalletORM).filter(WalletORM.user_id == user_id).first()

    def get_by_public_key(self, public_key: str) -> Optional[WalletORM]:
        """Look up a wallet by Stellar public key."""
        return self.db.query(WalletORM).filter(WalletORM.public_key == public_key).first()

    # ── Update ────────────────────────────────────────────────────────────

    def update(
        self,
        wallet: WalletORM,
        *,
        funded: Optional[bool] = None,
        trustline_ready: Optional[bool] = None,
        active: Optional[bool] = None,
    ) -> WalletORM:
        """Update mutable fields and refresh cached_at."""
        now = datetime.now(UTC)
        if funded is not None:
            wallet.funded = funded
        if trustline_ready is not None:
            wallet.trustline_ready = trustline_ready
        if active is not None:
            wallet.active = active
        wallet.last_updated = now
        wallet.cached_at = now
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def touch_cache(self, wallet: WalletORM) -> WalletORM:
        """Refresh only the cached_at timestamp (simulates a live re-fetch)."""
        wallet.cached_at = datetime.now(UTC)
        self.db.commit()
        self.db.refresh(wallet)
        return wallet
