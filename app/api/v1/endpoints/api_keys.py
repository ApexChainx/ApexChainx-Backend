from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.models.auth import AuthUser
from app.services.api_key_store import create_api_key, list_api_keys, revoke_key
from app.services.audit_log import audit_log

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


# Canonical scope registry — matches the scope table documented in docs/API.md.
# Keys must be created with scopes from this set; anything else is a typo and
# is rejected at creation time (422) so a mistyped scope can neither silently
# fail everywhere nor grant an unintended permission (#270).
KNOWN_SCOPES = frozenset(
    {
        "outages:read",
        "outages:write",
        "sla:read",
        "sla:write",
        "payments:read",
        "payments:write",
        "webhooks:read",
        "webhooks:write",
        "admin:full",
    }
)


def _is_expired(expires_at: datetime | None) -> bool:
    """True when the key has a past-or-present expiry (i.e. is dead)."""
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _key_status(revoked_at, expires_at) -> str:
    """Effective key status: revoked > expired > active."""
    if revoked_at is not None:
        return "revoked"
    if _is_expired(expires_at):
        return "expired"
    return "active"


class ApiKeyCreateRequest(BaseModel):
    name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, scopes: list[str]) -> list[str]:
        unknown = sorted(set(scopes) - KNOWN_SCOPES)
        if unknown:
            raise ValueError(f"Unknown API key scope(s): {', '.join(unknown)}")
        return scopes

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, expires_at: datetime | None) -> datetime | None:
        if expires_at is not None:
            candidate = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
            if candidate <= datetime.now(UTC):
                raise ValueError("expires_at must be in the future")
        return expires_at


class ApiKeyCreateResponse(BaseModel):
    id: str
    name: str | None
    raw_key: str
    message: str = "Store this key securely. It will not be shown again."


class ApiKeyItem(BaseModel):
    id: str
    name: str | None
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    created_by: str
    status: str


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeyItem]


class ApiKeyRevokeResponse(BaseModel):
    message: str


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def create_api_key_endpoint(
    payload: ApiKeyCreateRequest,
    current_user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    orm, raw_key = create_api_key(
        db=db,
        name=payload.name,
        scopes=payload.scopes,
        created_by=current_user.email,
        expires_at=payload.expires_at,
    )
    audit_log.log_event(
        db,
        "api_key_created",
        actor_id=current_user.id,
        details={
            "key_id": orm.id,
            "name": payload.name,
            "scopes": payload.scopes,
            "created_by": current_user.email,
        },
    )
    return ApiKeyCreateResponse(
        id=orm.id,
        name=orm.name,
        raw_key=raw_key,
    )


@router.get("", response_model=ApiKeyListResponse)
def list_api_keys_endpoint(
    current_user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    keys = list_api_keys(db)
    items = [
        ApiKeyItem(
            id=k.id,
            name=k.name,
            scopes=k.scopes or [],
            expires_at=k.expires_at,
            revoked_at=k.revoked_at,
            created_at=k.created_at,
            created_by=k.created_by,
            status=_key_status(k.revoked_at, k.expires_at),
        )
        for k in keys
    ]
    return ApiKeyListResponse(keys=items)


@router.delete("/{key_id}", response_model=ApiKeyRevokeResponse)
def revoke_api_key_endpoint(
    key_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    success = revoke_key(db, key_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )
    audit_log.log_event(
        db,
        "api_key_revoked",
        actor_id=current_user.id,
        details={
            "key_id": key_id,
            "revoked_by": current_user.email,
        },
    )
    return ApiKeyRevokeResponse(message="API key revoked successfully")
