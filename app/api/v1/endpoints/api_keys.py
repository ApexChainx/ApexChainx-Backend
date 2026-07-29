from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import require_admin
from app.models.auth import AuthUser
from app.services.api_key_store import create_api_key, list_api_keys, revoke_key
from app.services.audit_log import audit_log

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyCreateRequest(BaseModel):
    name: Optional[str] = None
    scopes: list[str] = []
    expires_at: Optional[datetime] = None


class ApiKeyCreateResponse(BaseModel):
    id: str
    name: Optional[str]
    raw_key: str
    message: str = "Store this key securely. It will not be shown again."


class ApiKeyItem(BaseModel):
    id: str
    name: Optional[str]
    scopes: list[str]
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    created_at: datetime
    created_by: str


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
