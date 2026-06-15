from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSON

from app.db.base import Base


class OutageORM(Base):
    __tablename__ = "outages"

    id = Column(String, primary_key=True, index=True)
    site_name = Column(String(255), nullable=False)
    site_id = Column(String(255), nullable=True)
    severity = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default="open", index=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    description = Column(Text, nullable=False)
    affected_services = Column(ARRAY(String), nullable=False, default=list)
    affected_subscribers = Column(Integer, nullable=True)
    assigned_to = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    location = Column(JSON, nullable=True)          # {"latitude": float, "longitude": float}
    sla_status = Column(JSON, nullable=True)        # SLAStatus dict
    mttr_minutes = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now(timezone.utc),
        onupdate=datetime.now(timezone.utc),
    )
