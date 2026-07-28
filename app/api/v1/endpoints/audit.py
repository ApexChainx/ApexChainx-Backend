from fastapi import APIRouter, Depends
from app.db.session import get_db
from app.models.orm.audit_log import AuditLogORM
from app.services.audit_log import audit_log
from app.core.security import require_admin
import hashlib
import json

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def get_audit_log(current_user=Depends(require_admin)):
    return audit_log.list()


@router.get("/verify")
def verify_audit_chain(current_user=Depends(require_admin)):
    db = next(get_db())
    try:
        entries = db.query(AuditLogORM).order_by(AuditLogORM.id.asc()).all()
        total = len(entries)
        if total == 0:
            return {"verified": True, "first_bad_id": None, "total_entries": 0, "last_hash": None}

        for i, entry in enumerate(entries):
            prev_hash = entries[i - 1].entry_hash if i > 0 else None
            data = {
                "prev_hash": prev_hash,
                "event_type": entry.event_type,
                "details": entry.details,
                "correlation_id": entry.correlation_id,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
            }
            expected_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True, default=str).encode()
            ).hexdigest()

            if entry.prev_hash != prev_hash or entry.entry_hash != expected_hash:
                return {
                    "verified": False,
                    "first_bad_id": entry.id,
                    "total_entries": total,
                    "last_hash": entries[-1].entry_hash,
                }

        return {
            "verified": True,
            "first_bad_id": None,
            "total_entries": total,
            "last_hash": entries[-1].entry_hash,
        }
    finally:
        db.close()