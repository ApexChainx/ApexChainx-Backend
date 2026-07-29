from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.security import hash_token
from app.models.orm.api_key import ApiKeyORM


def _now() -> datetime:
    return datetime.now(UTC)


def _generate_id() -> str:
    return str(uuid4())


def generate_api_key() -> tuple[str, str]:
    raw_key = "ak_" + secrets.token_urlsafe(32)
    hashed = hash_token(raw_key)
    return raw_key, hashed


def create_api_key(
    db: Session,
    name: str | None,
    scopes: list[str],
    created_by: str,
    expires_at: datetime | None = None,
) -> tuple[ApiKeyORM, str]:
    raw_key, hashed = generate_api_key()
    key_id = _generate_id()
    orm = ApiKeyORM(
        id=key_id,
        hashed_key=hashed,
        name=name,
        scopes=scopes,
        expires_at=expires_at,
        created_by=created_by,
        created_at=_now(),
    )
    db.add(orm)
    db.commit()
    db.refresh(orm)
    return orm, raw_key


def get_key_by_hash(db: Session, hashed_key: str) -> Optional[ApiKeyORM]:
    return db.query(ApiKeyORM).filter(ApiKeyORM.hashed_key == hashed_key).first()


def revoke_key(db: Session, key_id: str) -> bool:
    key = db.query(ApiKeyORM).filter(ApiKeyORM.id == key_id).first()
    if not key:
        return False
    key.revoked_at = _now()
    db.commit()
    return True


def list_api_keys(db: Session) -> list[ApiKeyORM]:
    return db.query(ApiKeyORM).order_by(ApiKeyORM.created_at.desc()).all()


def is_revoked_cached(key_id: str, redis_client) -> bool:
    cache_key = f"api_key:revoked:{key_id}"
    cached = redis_client.get(cache_key)
    if cached is not None:
        return cached == b"1"
    return False
