from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, String, Integer, JSON
from app.db.base import Base


class AuditLogORM(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    event_type = Column(String(100), index=True, nullable=False)
    # BE-010: Actor attribution - who performed the action
    email = Column(String(255), index=True, nullable=True)
    actor_id = Column(String(255), index=True, nullable=True)
    # BE-010: Correlation context - request correlation ID
    correlation_id = Column(String(255), index=True, nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    # BE-007: Cryptographic chaining for tamper-evident audit logs
    prev_hash = Column(String(64), nullable=True)
    entry_hash = Column(String(64), nullable=False, unique=True)
