from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limiter import rate_limiter
from app.core.security import get_current_user, hash_token, require_admin
from app.db.session import get_db
from app.models.auth import (
    AuthLogoutResponse,
    AuthSessionResponse,
    AuthUser,
    LoginRequest,
    LogoutAllSessionsResponse,
    ProfileUpdateRequest,
    RegisterRequest,
    SessionInfo,
    SessionInventoryResponse,
)
from app.repositories.user_repository import UserRepository, user_orm_to_pydantic
from app.services.auth_store import AuthStore
from app.services.credential_stuffing_detector import credential_stuffing_detector
from app.services.token_revocation import revoke
from app.utils.wallet_address import WalletAddressError, normalize as normalize_wallet

router = APIRouter()

"""
Auth Rate Limiting and Lockout Strategy:

1. IP-based Rate Limiting:
   - Max 10 requests per 5-minute window per IP for login/refresh endpoints
   - Returns 429 Too Many Requests when exceeded

2. Account Lockout:
   - After 5 failed login attempts, account is locked for 15 minutes
   - Failed attempts reset on successful login
   - Refresh tokens are also blocked for locked accounts

3. Audit Logging:
   - All failed attempts are logged
   - Account lockouts are logged with duration

Configuration (in app.core.config):
- AUTH_MAX_FAILED_ATTEMPTS: 5
- AUTH_LOCKOUT_DURATION_MINUTES: 15
- AUTH_RATE_LIMIT_REQUESTS: 10
- AUTH_RATE_LIMIT_WINDOW_SECONDS: 300
"""


def _get_client_ip(request: Request) -> str:
    """
    Extract the real client IP, respecting TRUSTED_PROXY_COUNT.

    When TRUSTED_PROXY_COUNT > 0 the app sits behind that many trusted proxy
    hops.  We take the Nth-from-the-right entry in X-Forwarded-For (where N =
    TRUSTED_PROXY_COUNT) so that a client cannot spoof its IP by injecting
    extra entries at the left of the header.

    When TRUSTED_PROXY_COUNT == 0 (default) we ignore forwarded headers
    entirely and use the direct connection address.
    """
    trusted = settings.TRUSTED_PROXY_COUNT
    if trusted > 0:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",")]
            # The rightmost `trusted` entries are added by our own proxies.
            # The entry just to the left of those is the real client.
            idx = max(len(parts) - trusted, 0)
            return parts[idx]
    return request.client.host if request.client else "unknown"


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    return authorization[len(prefix) :]


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/register", response_model=AuthUser, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = _get_client_ip(request)
    if not rate_limiter.is_allowed(f"register_ip_{client_ip}"):
        raise HTTPException(
            status_code=429,
            detail="Too many registration attempts from this IP. Please try again later.",
        )

    try:
        return AuthStore.register(payload, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login", response_model=AuthSessionResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    from app.services.audit_log import audit_log

    client_ip = _get_client_ip(request)

    # Credential stuffing detection
    credential_stuffing_detector.record_attempt(client_ip, payload.password)
    if credential_stuffing_detector.detect_stuffing(client_ip):
        lockout_minutes = settings.AUTH_LOCKOUT_DURATION_MINUTES * 4
        audit_log.log_event(
            db,
            "suspicious_login_activity",
            details={
                "ip": client_ip,
                "unique_prefix_count": credential_stuffing_detector.get_suspicious_ip_count(client_ip),
                "action": f"account_locked_{lockout_minutes}_minutes",
            },
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts from this IP. Account locked for {lockout_minutes} minutes.",
        )

    # Rate limit by IP
    if not rate_limiter.is_allowed(f"login_ip_{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many login attempts from this IP. Please try again later.")

    try:
        return AuthStore.login(payload, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/refresh", response_model=AuthSessionResponse)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = _get_client_ip(request)

    # Rate limit by IP
    if not rate_limiter.is_allowed(f"refresh_ip_{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many refresh attempts from this IP. Please try again later.")

    try:
        return AuthStore.refresh(payload.refresh_token, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me", response_model=AuthUser)
def me(current_user: AuthUser = Depends(get_current_user)):
    return current_user


@router.patch("/me/profile", response_model=AuthUser)
def update_profile(
    payload: ProfileUpdateRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update mutable profile fields (full_name, stellar_wallet). Role and email are immutable here."""
    if payload.full_name is None and payload.stellar_wallet is None:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    if payload.stellar_wallet is not None:
        try:
            normalize_wallet(payload.stellar_wallet)
        except WalletAddressError as exc:
            raise HTTPException(status_code=422, detail=exc.reason) from exc

    repo = UserRepository(db)
    updated = repo.update_profile(
        user_id=current_user.id,
        full_name=payload.full_name,
        stellar_wallet=payload.stellar_wallet,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    from app.services.audit_log import audit_log

    changed = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    audit_log.log_event(
        db, "profile_updated", email=current_user.email, details={"changed_fields": list(changed.keys())}
    )

    return user_orm_to_pydantic(updated)


@router.post("/logout", response_model=AuthLogoutResponse)
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    token = _extract_bearer_token(authorization)
    AuthStore.logout(token, db=db)
    return AuthLogoutResponse(message="Logged out successfully")


@router.get("/sessions", response_model=SessionInventoryResponse)
def get_session_inventory(
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all active sessions for the current user."""
    sessions = AuthStore.get_user_sessions(current_user.email, db=db)

    session_infos = [SessionInfo(**s) for s in sessions]
    active_count = sum(1 for s in session_infos if s.is_active)

    return SessionInventoryResponse(
        sessions=session_infos,
        total_count=len(session_infos),
        active_count=active_count,
    )


@router.get("/admin/sessions/{email}", response_model=SessionInventoryResponse)
def get_admin_session_inventory(
    email: str,
    admin_user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin endpoint to get all sessions for a specific user."""
    sessions = AuthStore.get_user_sessions(email, db=db)

    session_infos = [SessionInfo(**s) for s in sessions]
    active_count = sum(1 for s in session_infos if s.is_active)

    return SessionInventoryResponse(
        sessions=session_infos,
        total_count=len(session_infos),
        active_count=active_count,
    )


@router.post("/logout-all", response_model=LogoutAllSessionsResponse)
def logout_all_sessions(
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Invalidate all active sessions for the current user."""
    count = AuthStore.logout_all_sessions(current_user.email, db=db)
    return LogoutAllSessionsResponse(
        message=f"Logged out from {count} session(s)",
        sessions_invalidated=count,
    )


@router.post("/admin/logout-all/{email}", response_model=LogoutAllSessionsResponse)
def admin_logout_all_sessions(
    email: str,
    admin_user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin endpoint to invalidate all sessions for a specific user."""
    count = AuthStore.logout_all_sessions(email, db=db)
    return LogoutAllSessionsResponse(
        message=f"Logged out user {email} from {count} session(s)",
        sessions_invalidated=count,
    )


@router.get("/ping")
def auth_ping():
    return {"message": "auth ok"}


# --------------------------------------------------------------------------- #
# GDPR Endpoints                                                              #
# --------------------------------------------------------------------------- #


class GDPREraseResponse(BaseModel):
    status: str
    job_id: str
    message: str


@router.post("/me/export")
def export_my_data(
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export all personal data for the authenticated user (GDPR compliance).

    Returns a streaming gzip tarball containing user data and audit log entries.
    """
    from fastapi.responses import StreamingResponse

    from app.services.gdpr import export_user_data

    repo = UserRepository(db)
    user_orm = repo.get_by_id(current_user.id)
    if not user_orm:
        raise HTTPException(status_code=404, detail="User not found")

    tarball_bytes = export_user_data(db, user_orm)

    def _iter():
        yield tarball_bytes

    return StreamingResponse(
        _iter(),
        media_type="application/gzip",
        headers={"Content-Disposition": "attachment; filename=gdpr_export.tar.gz"},
    )


@router.post("/me/erase", response_model=GDPREraseResponse, status_code=status.HTTP_202_ACCEPTED)
def erase_my_data(
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-delete the authenticated user account (GDPR right-to-erasure).

    Personal data is pseudonymised and all active sessions are revoked.
    Returns 202 Accepted with a job id for tracking.
    """
    from app.services.gdpr import erase_user_data

    repo = UserRepository(db)
    user_orm = repo.get_by_id(current_user.id)
    if not user_orm:
        raise HTTPException(status_code=404, detail="User not found")

    result = erase_user_data(db, user_orm)
    return result


# --------------------------------------------------------------------------- #
# Impersonation Endpoint                                                      #
# --------------------------------------------------------------------------- #


class ImpersonateRequest(BaseModel):
    user_id: str
    reason: str = Field(..., min_length=1, description="Mandatory reason for impersonation")


class ImpersonateResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900  # 15 minutes for impersonation tokens
    acting_as: str


@router.post("/impersonate", response_model=ImpersonateResponse)
def impersonate_user(
    payload: ImpersonateRequest,
    admin_user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin endpoint to impersonate a non-admin user (audit-logged).

    Returns a short-lived JWT (15 min) with an ``act`` claim set to the
    admin's id so that every action performed during impersonation is
    attributable.

    Acceptance criteria:
    - Cannot impersonate another admin
    - Reason is mandatory and recorded in the audit log
    """
    from app.services.audit_log import audit_log

    repo = UserRepository(db)
    target = repo.get_by_id(payload.user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target user not found")

    if target.role == "admin":
        raise HTTPException(
            status_code=403,
            detail="Cannot impersonate another admin user",
        )

    token = _generate_impersonation_token(target, admin_user)

    audit_log.log_event(
        db,
        "impersonation_started",
        email=admin_user.email,
        actor_id=admin_user.id,
        details={
            "target_user_id": target.id,
            "target_email": target.email,
            "reason": payload.reason,
        },
    )

    return ImpersonateResponse(access_token=token, acting_as=target.id)


def _generate_impersonation_token(target_orm, admin_user: AuthUser) -> str:
    """Generate a short-lived impersonation access token."""
    import base64
    import hashlib
    import hmac
    import json
    import time

    from app.core.config import settings as app_settings

    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()

    now = int(time.time())
    payload_dict = {
        "sub": target_orm.id,
        "email": target_orm.email,
        "act": admin_user.id,  # acting admin
        "iat": now,
        "exp": now + 900,  # 15 minutes
        "scope": "impersonate",
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_dict).encode()).rstrip(b"=").decode()

    signing_key = (app_settings.IMPERSONATION_SIGNING_KEY or app_settings.SECRET_KEY or "apexchainx-dev-secret").encode()
    signature = hmac.new(
        signing_key,
        f"{header}.{payload}".encode(),
        hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

    return f"{header}.{payload}.{sig_b64}"


class RevokeResponse(BaseModel):
    message: str


@router.post("/revoke", response_model=RevokeResponse)
def revoke_token(
    authorization: str | None = Header(default=None),
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke the current access token. Subsequent requests with this token
    will receive 401 'Token revoked' response."""
    from app.services.auth_store import TOKEN_TTL_SECONDS

    token = _extract_bearer_token(authorization)
    revoke(hash_token(token), TOKEN_TTL_SECONDS)
    from app.services.audit_log import audit_log

    audit_log.log_event(db, "token_revoked", email=current_user.email, actor_id=current_user.id)
    return RevokeResponse(message="Token revoked successfully")
