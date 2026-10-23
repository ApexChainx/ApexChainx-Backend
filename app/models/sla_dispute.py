import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class DisputeStatus(str, PyEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class SLADispute(Base):
    __tablename__ = "sla_disputes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sla_result_id = Column(Integer, ForeignKey("sla_results.id"), nullable=False, index=True)
    baseline_sla_result_id = Column(Integer, ForeignKey("sla_results.id"), nullable=True)
    proposed_sla_result_id = Column(Integer, ForeignKey("sla_results.id"), nullable=True)

    # Dispute metadata
    flagged_by = Column(String(255), nullable=False)
    dispute_reason = Column(Text, nullable=False)
    flagged_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    # Resolution metadata
    status = Column(Enum(DisputeStatus), default=DisputeStatus.PENDING, nullable=False)
    resolved_by = Column(String(255), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    sla_result = relationship("SLAResultORM", foreign_keys=[sla_result_id], back_populates="disputes")
    baseline_sla_result = relationship("SLAResultORM", foreign_keys=[baseline_sla_result_id])
    proposed_sla_result = relationship("SLAResultORM", foreign_keys=[proposed_sla_result_id])
    audit_logs = relationship("DisputeAuditLog", back_populates="dispute", order_by="DisputeAuditLog.recorded_at")


class DisputeAuditLog(Base):
    __tablename__ = "dispute_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dispute_id = Column(UUID(as_uuid=True), ForeignKey("sla_disputes.id"), nullable=False, index=True)
    action = Column(String(50), nullable=False)  # e.g. "flagged", "resolved", "rejected"
    actor = Column(String(255), nullable=False)
    notes = Column(Text, nullable=True)
    recorded_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    dispute = relationship("SLADispute", back_populates="audit_logs")
