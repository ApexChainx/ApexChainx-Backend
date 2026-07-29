from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text

from app.db.base import Base

CURRENT_SCHEMA_VERSION = "1"


class OutageEventORM(Base):
    __tablename__ = "outage_events"

    id = Column(String, primary_key=True, index=True)
    outage_id = Column(String, ForeignKey("outages.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(100), nullable=False)  # e.g. "created", "resolved", "sla_computed", "recomputed"
    detail = Column(Text, nullable=True)
    schema_version = Column(
        String(10), nullable=False, default=CURRENT_SCHEMA_VERSION, server_default=CURRENT_SCHEMA_VERSION
    )
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
