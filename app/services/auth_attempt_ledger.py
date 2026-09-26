import hashlib
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.auth_attempt import AuthAttemptLedger


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prune_scope(db: Session, scope_hash: str, cutoff: datetime) -> None:
    db.query(AuthAttemptLedger).filter(
        AuthAttemptLedger.scope_hash == scope_hash,
        AuthAttemptLedger.occurred_at < cutoff,
    ).delete(synchronize_session=False)


def _prune_expired(db: Session, now: datetime) -> None:
    retention_seconds = max(
        settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
        settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60,
    )
    db.query(AuthAttemptLedger).filter(
        AuthAttemptLedger.occurred_at < now - timedelta(seconds=retention_seconds)
    ).delete(synchronize_session=False)


def record_rate_limit_attempt(
    db: Session,
    key: str,
    max_attempts: int,
    window_seconds: int,
) -> bool:
    now = datetime.utcnow()
    _prune_expired(db, now)
    scope_hash = _digest(f"rate:{key}")
    cutoff = now - timedelta(seconds=window_seconds)
    _prune_scope(db, scope_hash, cutoff)

    count = (
        db.query(func.count(AuthAttemptLedger.id))
        .filter(
            AuthAttemptLedger.scope_hash == scope_hash,
            AuthAttemptLedger.occurred_at >= cutoff,
        )
        .scalar()
        or 0
    )
    if count >= max_attempts:
        db.commit()
        return False

    db.add(
        AuthAttemptLedger(
            scope_hash=scope_hash,
            attempt_hash=uuid4().hex,
            occurred_at=now,
        )
    )
    db.commit()
    return True


def record_credential_prefix(
    db: Session,
    ip: str,
    password: str,
    max_prefixes: int,
) -> None:
    now = datetime.utcnow()
    _prune_expired(db, now)
    scope_hash = _digest(f"stuffing:{ip}")
    prefix_hash = _digest(password[:4])
    cutoff = now - timedelta(
        minutes=settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES
    )
    _prune_scope(db, scope_hash, cutoff)

    existing = (
        db.query(AuthAttemptLedger)
        .filter(
            AuthAttemptLedger.scope_hash == scope_hash,
            AuthAttemptLedger.attempt_hash == prefix_hash,
        )
        .first()
    )
    if existing:
        existing.occurred_at = now
    else:
        count = (
            db.query(func.count(AuthAttemptLedger.id))
            .filter(
                AuthAttemptLedger.scope_hash == scope_hash,
                AuthAttemptLedger.occurred_at >= cutoff,
            )
            .scalar()
            or 0
        )
        if count >= max_prefixes:
            db.commit()
            return
        db.add(
            AuthAttemptLedger(
                scope_hash=scope_hash,
                attempt_hash=prefix_hash,
                occurred_at=now,
            )
        )
    db.commit()


def get_credential_prefix_count(db: Session, ip: str, window_seconds: int) -> int:
    now = datetime.utcnow()
    _prune_expired(db, now)
    scope_hash = _digest(f"stuffing:{ip}")
    cutoff = now - timedelta(seconds=window_seconds)
    _prune_scope(db, scope_hash, cutoff)
    count = (
        db.query(func.count(AuthAttemptLedger.id))
        .filter(
            AuthAttemptLedger.scope_hash == scope_hash,
            AuthAttemptLedger.occurred_at >= cutoff,
        )
        .scalar()
        or 0
    )
    db.commit()
    return int(count)