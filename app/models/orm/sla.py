from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class SLAResultORM(Base):
    __tablename__ = "sla_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    outage_id = Column(String, ForeignKey("outages.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False)           # "met" | "violated"
    mttr_minutes = Column(Integer, nullable=False)
    threshold_minutes = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    payment_type = Column(String(20), nullable=False)     # "reward" | "penalty"
    rating = Column(String(20), nullable=False)           # "exceptional" | "excellent" | "good" | "poor"
    policy_version = Column(String(50), nullable=False, default="1.0")
    threshold_source = Column(String(50), nullable=False, default="config")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    is_latest = Column(Boolean, nullable=False, default=False)
    reason_code = Column(String(50), nullable=True)       # e.g., "mttr_exceeded", "met_exceptional"
    decision_trace = Column(Text, nullable=True)          # Machine-readable decision trace

    disputes = relationship("SLADispute", back_populates="sla_result")

    __table_args__ = (
        Index("ix_sla_results_outage_latest", "outage_id", "is_latest"),
    )
