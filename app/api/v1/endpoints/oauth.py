"""OAuth 2.0 authorization endpoints with PKCE and exact-match redirect_uri validation."""

import secrets

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.services.audit_log import audit_log
from app.services.oauth_session import oauth_state_repo

router = APIRouter(prefix="/oauth", tags=["oauth"])

PROVIDERS = {"google", "github", "gitlab"}


@router.get("/{provider}/authorize")
def authorize(provider: str, redirect_uri: str = Query(...), code_challenge: str | None = Query(None)):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    if redirect_uri not in settings.OAUTH_REDIRECT_URI_ALLOWLIST:
        raise HTTPException(status_code=400, detail="invalid_redirect")
    code_verifier = secrets.token_urlsafe(32) if code_challenge else None
    state = oauth_state_repo.create_state(provider, redirect_uri, code_verifier)
    auth_url = f"/api/v1/oauth/{provider}/callback?state={state}&redirect_uri={redirect_uri}"
    if code_challenge:
        auth_url += f"&code_challenge={code_challenge}"
    return {"authorization_url": auth_url, "state": state}


@router.get("/{provider}/callback")
def callback(
    provider: str, state: str = Query(...), code: str | None = Query(None), code_verifier: str | None = Query(None)
):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    if not state:
        raise HTTPException(status_code=400, detail="state_required")
    stored = oauth_state_repo.consume_state(state)
    if stored is None:
        raise HTTPException(status_code=400, detail="invalid_or_expired_state")
    if stored["provider"] != provider:
        raise HTTPException(status_code=400, detail="provider_mismatch")
    audit_log.log("oauth_callback", {"provider": provider, "actor": f"oauth:{provider}"})
    return {"status": "ok", "provider": provider, "message": "Authorization successful"}
