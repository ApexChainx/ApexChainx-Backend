from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.orm.audit_log import AuditLogORM
from app.db.session import SessionLocal, AuditSessionLocal
from app.core.config import settings
from app.utils.correlation import get_correlation_id

class AuditLogService:
    def __init__(self, db_session_factory=None):
        self.db_session_factory = db_session_factory or SessionLocal

    def log_event(
        self,
        db: Session,
        event_type: str,
        email: Optional[str] = None,
        actor_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None
    ) -> None:
        """
        Records a structured audit event with actor attribution and correlation context.
        
        BE-010 Enhancements:
        - actor_id: Consistent user identifier for cross-event correlation
        - correlation_id: Request correlation ID to link related events
        - Sensitive data sanitization to prevent secret leakage
        
        Args:
            db: Database session
            event_type: Type of event (e.g., 'login_success', 'job_retry_initiated')
            email: User email (legacy field, kept for backward compatibility)
            actor_id: User ID for consistent actor tracking (preferred over email)
            details: Event details dict (will be sanitized)
            correlation_id: Request correlation ID from context (auto-detected if not provided)
        """
        # Sanitization: prevent leaking common sensitive keys
        safe_details = details.copy() if details else {}
        sensitive_keys = {"password", "token", "access_token", "refresh_token", "secret", "secret_key"}
        for key in sensitive_keys:
            if key in safe_details:
                safe_details[key] = "[REDACTED]"
        
        # Auto-detect correlation_id from request context if not provided
        if correlation_id is None:
            correlation_id = get_correlation_id()

        audit_entry = AuditLogORM(
            event_type=event_type,
            email=email,
            actor_id=actor_id,
            correlation_id=correlation_id,
            details=safe_details,
            created_at=datetime.now(timezone.utc)
        )
        db.add(audit_entry)
        db.commit()

    def log(self, event_type: str, details: Optional[dict[str, Any]] = None) -> None:
        """
        Simplified log method for compatibility with existing code.
        Uses its own session if not provided.
        
        When DATABASE_AUDIT_URL is configured, writes go through the
        audit-specific DB role/connection for least-privilege isolation.
        """
        factory = AuditSessionLocal if settings.DATABASE_AUDIT_URL else self.db_session_factory
        with factory() as db:
            self.log_event(db, event_type, details=details)

# Create a singleton instance for common use
audit_log = AuditLogService()
