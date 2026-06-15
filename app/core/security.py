import hashlib
import re
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
    token = _extract_bearer_token(authorization)
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
