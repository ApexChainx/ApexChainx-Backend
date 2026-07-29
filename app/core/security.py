import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from passlib.context import CryptContext
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.models.auth import AuthUser
from app.models.enums import Role
from app.db.session import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def hash_token(token: str) -> str:
    """Return a SHA-256 hex digest of a token for secure storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def validate_password_policy(password: str) -> bool:
    """
    Enforce a password policy:
    - At least 8 characters long
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    """
    if len(password) < 8:
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False
    return True


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    return authorization[len(prefix) :]


def get_current_user(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> AuthUser:
    from app.services.auth_store import AuthStore
    from app.services.token_revocation import is_revoked
    token = _extract_bearer_token(authorization)
    if is_revoked(hash_token(token)):
        raise HTTPException(status_code=401, detail="Token revoked")
    user = AuthStore.get_user_for_token(token, db=db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def require_role(required_role: Role):
    def dependency(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if current_user.role != required_role:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required role: {required_role.value}"
            )
        return current_user
    return dependency


# Convenience dependencies for common roles
require_admin = require_role(Role.admin)
require_engineer = require_role(Role.engineer)


def get_current_user_or_service(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Accepts either:
      - Authorization: Bearer <token>  (authenticated user)
      - X-Api-Key: ak_***              (service-to-service)
    Returns a dict with actor info for audit logging.
    """
    if x_api_key:
        from app.services.api_key_store import get_key_by_hash
        hashed = hash_token(x_api_key)
        key = get_key_by_hash(db, hashed)
        if not key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        if key.revoked_at is not None:
            raise HTTPException(status_code=401, detail="API key has been revoked")
        if key.expires_at is not None and key.expires_at.replace(tzinfo=None) < datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(status_code=401, detail="API key has expired")
        return {
            "actor_type": "service",
            "actor_id": f"service:{key.id}",
            "key_id": key.id,
            "scopes": key.scopes or [],
        }
    if authorization:
        user = get_current_user(authorization=authorization, db=db)
        return {
            "actor_type": "user",
            "actor_id": user.id,
            "email": user.email,
            "role": user.role,
            "scopes": [],
        }
    raise HTTPException(status_code=401, detail="Missing Authorization or X-Api-Key header")


def require_scope(required_scope: str):
    def dependency(actor: dict[str, Any] = Depends(get_current_user_or_service)) -> dict[str, Any]:
        scopes = actor.get("scopes", [])
        if required_scope not in scopes:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient scope. Required scope: {required_scope}",
            )
        return actor
    return dependency
