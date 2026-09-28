from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSON
from app.db.base import Base


# ARRAY is Postgres-only; the wallet repository tests build schemas on
# in-memory SQLite via Base.metadata.create_all, which would die on
# "SQLiteTypeCompiler has no attribute visit_ARRAY". JSON survives both
# dialects, and on Postgres it stays queryable with JSON operators. The ORM
# already treats the column as a Python list, so the app-facing behaviour is
# unchanged.
def _affected_services_type():
    return JSON().with_variant(ARRAY(String), "postgresql")


class OutageORM(Base):
    __tablename__ = "outages"

    id = Column(String, primary_key=True, index=True)
    site_name = Column(String(255), nullable=False)
    site_id = Column(String(255), nullable=True)
    severity = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default="open", index=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(UTC))
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    description = Column(Text, nullable=False)
    affected_services = Column(_affected_services_type(), nullable=False, default=list)
    affected_subscribers = Column(Integer, nullable=True)
    assigned_to = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    location = Column(JSON, nullable=True)  # {"latitude": float, "longitude": float}
    sla_status = Column(JSON, nullable=True)  # SLAStatus dict
    mttr_minutes = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.now(UTC),
        onupdate=datetime.now(UTC),
    )
