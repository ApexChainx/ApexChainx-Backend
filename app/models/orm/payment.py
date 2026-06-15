from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String

from app.db.base import Base


class PaymentTransactionORM(Base):
    __tablename__ = "payment_transactions"

    id = Column(String, primary_key=True, index=True)
    transaction_hash = Column(String(255), nullable=False, unique=True)
    type = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    asset_code = Column(String(20), nullable=False)
    from_address = Column(String(255), nullable=False)
    to_address = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="pending", index=True)
    outage_id = Column(String, ForeignKey("outages.id", ondelete="SET NULL"), nullable=True, index=True)
    sla_result_id = Column(Integer, ForeignKey("sla_results.id", ondelete="SET NULL"), nullable=True, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    last_retried_at = Column(DateTime(timezone=True), nullable=True)
