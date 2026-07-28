import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from app.db.base_class import Base


class WebhookEvent(str, enum.Enum):
    SLA_VIOLATION = "sla.violation"
    SLA_WARNING = "sla.warning"
    SLA_RESOLVED = "sla.resolved"


class WebhookDeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"  # BE-086: Dead-letter status for permanently failed deliveries


class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    url = Column(String(2048), nullable=False)
    secret = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    events = Column(Text, nullable=False)  # JSON-encoded list of WebhookEvent values
    resolved_ips = Column(Text, nullable=True)  # JSON-encoded list of resolved IPs for SSRF validation
    max_retries = Column(Integer, default=3, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # BE-034: Secret lifecycle metadata
    last_secret_rotation_at = Column(DateTime, nullable=True)  # When the secret was last rotated
    secret_version = Column(Integer, default=1, nullable=False)  # Incremented on each rotation

    deliveries = relationship("WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    webhook_id = Column(UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False)
    event = Column(SAEnum(WebhookEvent), nullable=False)
    payload = Column(Text, nullable=False)  # JSON-encoded payload
    status = Column(SAEnum(WebhookDeliveryStatus), default=WebhookDeliveryStatus.PENDING, nullable=False)
    attempt_count = Column(Integer, default=0, nullable=False)
    next_retry_at = Column(DateTime, nullable=True)
    response_status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    dead_lettered_at = Column(DateTime, nullable=True)  # BE-086: When delivery was marked as dead-letter
    signature_version = Column(Integer, default=1, nullable=False)  # BE-087: Explicit signature algorithm version
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    webhook = relationship("Webhook", back_populates="deliveries")
