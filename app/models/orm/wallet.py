"""Wallet ORM model for Postgres-backed wallet persistence (#49).

Maps to the ``wallets`` table defined by migration ``0010_wallet_persistence``.
Uses the same Column-based declarative style as the rest of the codebase.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.db.base import Base


class WalletORM(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=False, unique=True)
    public_key = Column(String(56), nullable=False, unique=True)
    funded = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)
    trustline_ready = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    last_updated = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    cached_at = Column(DateTime(timezone=True), nullable=True)
