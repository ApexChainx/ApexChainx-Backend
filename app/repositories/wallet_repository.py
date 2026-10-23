"""Postgres-backed wallet persistence layer (issue #49).

Provides durable wallet identity with uniqueness enforcement:
- user_id is unique → concurrent register → 409
- public_key is unique → prevents double-linking

Issue #700 — SQL injection hardening:
    Input validation rejects control characters and known SQLi probe tokens
    before any value reaches SQLAlchemy.  Note: SQLAlchemy already uses
    parameterised queries, so this is defence-in-depth that also catches
    obviously malicious inputs early and returns a clear 422.

Issue #701 — Pagination:
    list_wallets() exposes an offset-based paginated view of all wallets
    using COUNT(*) OVER() so the total is computed in a single DB pass,
    matching the pattern used in OutageRepository.list().
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.orm.wallet import WalletORM

# ── Input validation helpers (#700) ───────────────────────────────────────────

# Printable ASCII plus hyphen/underscore/dot/@ – all legitimate user_id and
# public_key characters.  Anything else (null bytes, control chars, etc.)
# is rejected immediately.
_SAFE_INPUT_RE = re.compile(r"^[\x20-\x7E]+$")

# Short blocklist of tokens that appear in virtually every SQLi probe.  This is
# defence-in-depth only; SQLAlchemy parameterisation is the primary protection.
_SQLI_PATTERNS = re.compile(
    r"""(\b(union|select|insert|update|delete|drop|alter|exec|execute|
             declare|cast|convert|xp_cmdshell|waitfor|sleep)\b
        |--|;|/\*|\*/|'\s*or\s*'|'\s*and\s*')""",
    re.IGNORECASE | re.VERBOSE,
)


def _validate_input(value: str, field_name: str) -> None:
    """Raise ValueError if *value* contains dangerous characters or SQLi tokens.

    This runs before values are handed to SQLAlchemy so that obviously
    malicious inputs are surfaced early with a clear error message.
    """
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if not _SAFE_INPUT_RE.match(value):
        raise ValueError(
            f"{field_name} contains invalid characters. "
            "Only printable ASCII characters are allowed."
        )
    if _SQLI_PATTERNS.search(value):
        raise ValueError(
            f"{field_name} contains disallowed SQL keywords or characters."
        )


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
        _validate_input(user_id, "user_id")
        _validate_input(public_key, "public_key")

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

    def get_by_user_id(self, user_id: str) -> WalletORM | None:
        """Look up a wallet by user_id."""
        _validate_input(user_id, "user_id")
        return self.db.query(WalletORM).filter(WalletORM.user_id == user_id).first()

    def get_by_public_key(self, public_key: str) -> WalletORM | None:
        """Look up a wallet by Stellar public key."""
        _validate_input(public_key, "public_key")
        return self.db.query(WalletORM).filter(WalletORM.public_key == public_key).first()

    def list_wallets(
        self,
        page: int = 1,
        page_size: int = 20,
        include_total: bool = True,
    ) -> dict:
        """Return a paginated slice of wallets (issue #701).

        Uses COUNT(*) OVER() so the total count is computed in the same query
        pass as the page items — avoids a second round-trip to the DB,
        matching the pattern used by OutageRepository.list().

        Returns:
            {
                "items":     list[WalletORM],
                "total":     int | None,   # None when include_total=False
                "page":      int,
                "page_size": int,
            }
        """
        from sqlalchemy import func

        query = self.db.query(WalletORM).order_by(WalletORM.created_at.desc(), WalletORM.id.asc())

        offset = (page - 1) * page_size

        if include_total:
            rows = (
                query.add_columns(func.count().over())
                .offset(offset)
                .limit(page_size)
                .all()
            )
            total = rows[0][1] if rows else 0
            items = [row[0] for row in rows]
        else:
            total = None
            items = query.offset(offset).limit(page_size).all()

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ── Update ────────────────────────────────────────────────────────────

    def update(
        self,
        wallet: WalletORM,
        *,
        funded: bool | None = None,
        trustline_ready: bool | None = None,
        active: bool | None = None,
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
