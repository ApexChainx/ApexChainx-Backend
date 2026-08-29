import hashlib
import logging
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, Request
from jwt import InvalidTokenError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.db.session import get_db
from app.models.auth import AuthUser
from app.models.enums import Role
from app.services.metrics import increment_counter
from app.utils.correlation_ctx import get_correlation_id

logger = logging.getLogger(__name__)
IMPERSONATION_VERIFICATION_FAILURES = "impersonation_verification_failures"

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


def _verify_impersonation_token(token: str) -> dict[str, Any] | None:
    """Verify a short-lived impersonation JWT using PyJWT.

    Returns the decoded payload if valid, or None if invalid/expired.
    Every failure mode emits a structured log line with a machine-readable
    reason and the request correlation ID, and increments the
    ``impersonation_verification_failures`` counter tagged by reason, so
    scanning of this privileged surface is observable (#271).
    """
    try:
        secret = (app_settings.SECRET_KEY or "apexchainx-dev-secret")
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "scope"]},
        )
        if payload.get("scope") != "impersonate":
            _record_impersonation_failure("wrong_scope")
            return None
        return payload
    except jwt.ExpiredSignatureError:
        _record_impersonation_failure("expired")
    except jwt.InvalidAlgorithmError:
        _record_impersonation_failure("invalid_algorithm")
    except jwt.InvalidSignatureError:
        _record_impersonation_failure("bad_signature")
    except jwt.DecodeError:
        _record_impersonation_failure("malformed")
    except InvalidTokenError:
        _record_impersonation_failure("invalid_token")
    except Exception:
        _record_impersonation_failure("unexpected")
    return None


def _record_impersonation_failure(reason: str) -> None:
    """Record a failed impersonation verification without exposing token data."""
    correlation_id = get_correlation_id()
    logger.warning(
        "impersonation_verification_failed",
        extra={"reason": reason, "correlation_id": correlation_id},
    )
    increment_counter(IMPERSONATION_VERIFICATION_FAILURES, tags={"reason": reason})


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AuthUser:
    from app.repositories.user_repository import UserRepository, user_orm_to_pydantic
    from app.services.auth_store import AuthStore
    from app.services.token_revocation import is_revoked

    token = _extract_bearer_token(authorization)

    # Check for impersonation token first
    imp_payload = _verify_impersonation_token(token)
    if imp_payload:
        user_id = imp_payload.get("sub")
        if user_id:
            repo = UserRepository(db)
            user_orm = repo.get_by_id(user_id)
            if user_orm:
                user = user_orm_to_pydantic(user_orm)
                request.state.user = user
                return user

    if is_revoked(hash_token(token)):
        raise HTTPException(status_code=401, detail="Token revoked")
    user = AuthStore.get_user_for_token(token, db=db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # Stamp the authenticated actor so CorrelationMiddleware can attribute
    # access logs with a user_id_hash.
    request.state.user = user
    return user


def require_role(required_role: Role):
    def dependency(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if current_user.role != required_role:
            raise HTTPException(
                status_code=403, detail=f"Insufficient permissions. Required role: {required_role.value}"
            )
        return current_user

    return dependency


# Convenience dependencies for common roles
require_admin = require_role(Role.admin)
require_engineer = require_role(Role.engineer)


def require_engineer_or_admin(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Allow either an engineer or an admin to access an endpoint.

    Used by read-only dispute endpoints: engineers flag and admins resolve
    disputes, so both roles need read access to the dispute record.
    """
    if current_user.role not in (Role.engineer, Role.admin):
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions. Required role: engineer or admin",
        )
    return current_user


def get_current_user_or_service(
    request: Request,
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
        if key.expires_at is not None and key.expires_at.replace(tzinfo=None) < datetime.now(UTC).replace(tzinfo=None):
            raise HTTPException(status_code=401, detail="API key has expired")
        # Stamp the service actor (by key id) for access-log attribution.
        request.state.user = SimpleNamespace(id=key.id)
        return {
            "actor_type": "service",
            "actor_id": f"service:{key.id}",
            "key_id": key.id,
            "scopes": key.scopes or [],
        }
    if authorization:
        user = get_current_user(request=request, authorization=authorization, db=db)
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
