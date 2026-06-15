from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, String, Integer, Boolean, ForeignKey
from app.db.base import Base


class TokenFamilyORM(Base):
    __tablename__ = "token_families"

    family_id = Column(String(64), primary_key=True, index=True)
    email = Column(String(255), ForeignKey("users.email"), nullable=False, index=True)
    current_sequence = Column(Integer, nullable=False, default=0)
    compromised = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))
