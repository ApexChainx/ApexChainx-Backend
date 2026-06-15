from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import Column, DateTime, Float, Integer, String

from app.db.base import Base


class SLAAnalyticsSnapshotORM(Base):
    __tablename__ = "sla_analytics_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_key = Column(String(100), nullable=False, index=True)  # e.g. "global" or "severity:critical"
    total_outages = Column(Integer, nullable=False, default=0)
    total_violations = Column(Integer, nullable=False, default=0)
    total_rewards = Column(Float, nullable=False, default=0.0)
    total_penalties = Column(Float, nullable=False, default=0.0)
    net_payout = Column(Float, nullable=False, default=0.0)
    avg_mttr = Column(Float, nullable=False, default=0.0)
    checksum = Column(String(64), nullable=False)  # SHA-256 hash of the snapshot data
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))

    def compute_checksum(self) -> str:
        """Compute SHA-256 checksum of snapshot data (excluding id and checksum fields)."""
        data = {
            "snapshot_key": self.snapshot_key,
            "total_outages": self.total_outages,
            "total_violations": self.total_violations,
            "total_rewards": self.total_rewards,
            "total_penalties": self.total_penalties,
            "net_payout": self.net_payout,
            "avg_mttr": self.avg_mttr,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        # Use sorted keys to ensure consistent hashing
        sorted_json = json.dumps(data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(sorted_json).hexdigest()
