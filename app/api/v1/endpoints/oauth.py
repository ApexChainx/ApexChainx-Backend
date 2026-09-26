"""OAuth 2.0 authorization endpoints with PKCE and exact-match redirect_uri validation."""

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
    state = oauth_state_repo.create_state(provider, redirect_uri, code_challenge)
    auth_url = f"/api/v1/oauth/{provider}/callback?state={state}&redirect_uri={redirect_uri}"
    if code_challenge:
        auth_url += f"&code_challenge={code_challenge}"
    return {"authorization_url": auth_url, "state": state}


@router.get("/{provider}/callback")
def callback(
    provider: str,
    state: str = Query(...),
    code: str | None = Query(None),
    code_verifier: str | None = Query(None),
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
    code_challenge = stored.get("code_challenge")
    if code_challenge and (
        not code_verifier or not oauth_state_repo.verify_code_challenge(code_verifier, code_challenge)
    ):
        audit_log.log("oauth_callback_failed", {"provider": provider, "reason": "pkce_verification_failed"})
        raise HTTPException(status_code=400, detail="invalid_code_verifier")
    if not code_challenge and code_verifier:
        audit_log.log("oauth_callback_failed", {"provider": provider, "reason": "unexpected_code_verifier"})
        raise HTTPException(status_code=400, detail="invalid_code_verifier")

    audit_log.log("oauth_callback_failed", {"provider": provider, "reason": "provider_exchange_not_implemented"})
    raise HTTPException(status_code=501, detail="oauth_provider_exchange_not_implemented")
