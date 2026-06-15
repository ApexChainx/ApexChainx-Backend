from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, String, Integer, ForeignKey
from app.db.base import Base

class SessionORM(Base):
    __tablename__ = "sessions"

    access_token = Column(String(255), primary_key=True, index=True)
    refresh_token = Column(String(255), unique=True, index=True, nullable=False)
    email = Column(String(255), ForeignKey("users.email"), nullable=False)
    family_id = Column(String(64), ForeignKey("token_families.family_id"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
