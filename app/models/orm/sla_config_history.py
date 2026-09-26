from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Index, Integer, String

from app.db.base import Base


class SLAConfigHistoryORM(Base):
    __tablename__ = "sla_config_history"
    # Mirrors the unique index created in 0022_sla_config_history so the
    # (severity, policy_version) pair can never repeat (#272).
    __table_args__ = (Index("ix_sla_config_history_severity_version", "severity", "policy_version", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    severity = Column(String(20), nullable=False, index=True)
    policy_version = Column(Integer, nullable=False)
    threshold_minutes = Column(Integer, nullable=False)
    penalty_per_minute = Column(Integer, nullable=False)
    reward_base = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False)
    publish_token = Column(String(64), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    published_by = Column(String(255), nullable=True)
