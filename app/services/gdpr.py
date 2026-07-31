"""GDPR compliance service for user data export and right-to-erasure.

Provides:
- export_user_data(): Collects all user data into a tarball payload.
- erase_user_data(): Soft-deletes the user account and returns a job ID.
"""

import io
import json
import tarfile
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.orm.audit_log import AuditLogORM
from app.models.orm.user import UserORM
from app.repositories.session_repository import SessionRepository
from app.repositories.token_family_repository import TokenFamilyRepository
from app.services.audit_log import audit_log


def _serialize_datetime(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def export_user_data(db: Session, user: UserORM) -> dict[str, Any]:
    """Collect all personal data associated with a user.

    Returns a dict that can be serialised into a tarball for download.
    The export is designed to complete in < 30 s for up to 1 000 audit events.
    """
    user_data = {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "stellar_wallet": user.stellar_wallet,
        "created_at": _serialize_datetime(user.created_at),
    }

    # Collect audit log entries (limit to most recent 1 000 for performance)
    audit_entries = (
        db.query(AuditLogORM)
        .filter((AuditLogORM.email == user.email) | (AuditLogORM.actor_id == user.id))
        .order_by(AuditLogORM.created_at.desc())
        .limit(1000)
        .all()
    )
    audit_logs = [
        {
            "event_type": entry.event_type,
            "correlation_id": entry.correlation_id,
            "details": entry.details,
            "created_at": _serialize_datetime(entry.created_at),
        }
        for entry in audit_entries
    ]

    export_payload = {
        "exported_at": datetime.now(UTC).isoformat(),
        "user": user_data,
        "audit_logs": audit_logs,
        "audit_log_count": len(audit_logs),
    }

    # Build an in-memory tarball containing the JSON export
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        json_bytes = json.dumps(export_payload, indent=2, default=str).encode("utf-8")
        info = tarfile.TarInfo(name="user_data.json")
        info.size = len(json_bytes)
        tar.addfile(info, io.BytesIO(json_bytes))

    audit_log.log_event(
        db,
        "gdpr_export",
        email=user.email,
        actor_id=user.id,
        details={"export_size_bytes": tar_buffer.tell()},
    )

    return {
        "job_id": str(uuid.uuid4()),
        "exported_at": export_payload["exported_at"],
        "size_bytes": tar_buffer.tell(),
        "tarball_base64": tar_buffer.getvalue(),
        "entry_count": len(audit_logs),
    }


def erase_user_data(db: Session, user: UserORM) -> dict[str, Any]:
    """Soft-delete a user account in compliance with GDPR right-to-erasure.

    The account is deactivated rather than physically removed so that any
    financial / audit records remain referentially intact.  Personal fields
    are pseudonymised.

    Returns a 202-style payload with a job id for async tracking.
    """
    job_id = str(uuid.uuid4())
    original_email = user.email

    # Revoke all active sessions BEFORE pseudonymising the email
    session_repo = SessionRepository(db)
    token_family_repo = TokenFamilyRepository(db)
    session_repo.delete_sessions_by_email(original_email)
    token_family_repo.delete_families_by_email(original_email)

    # Pseudonymise personal fields
    user.email = f"erased-{user.id}@deleted.local"
    user.full_name = f"Erased User {user.id[:8]}"
    user.hashed_password = ""  # nosec B105 - erasing credential, not a hardcoded password
    user.stellar_wallet = None
    user.locked_until = datetime.now(UTC)
    db.commit()

    audit_log.log_event(
        db,
        "gdpr_erase",
        email=original_email,
        actor_id=user.id,
        details={"job_id": job_id, "action": "soft_delete"},
    )

    return {
        "status": "accepted",
        "job_id": job_id,
        "message": "Account erasure has been initiated. Personal data will be pseudonymised.",
    }
