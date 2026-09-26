from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from app.db.base_class import Base


class AuthAttemptLedger(Base):
    __tablename__ = "auth_attempt_ledger"
    __table_args__ = (
        UniqueConstraint("scope_hash", "attempt_hash", name="uq_auth_attempt_scope_hash"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_hash = Column(String(64), nullable=False, index=True)
    attempt_hash = Column(String(64), nullable=False)
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)