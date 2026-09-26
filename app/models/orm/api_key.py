from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, String

from app.db.base import Base


class ApiKeyORM(Base):
    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    hashed_key = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=True)
    scopes = Column(JSON, nullable=False, default=list)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(UTC))
    created_by = Column(String(255), nullable=False)
