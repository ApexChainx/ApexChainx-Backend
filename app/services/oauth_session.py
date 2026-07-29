"""OAuth session state management backed by Redis.

Stores OAuth state parameters with TTL for PKCE and anti-CSRF protection.
"""
import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Optional

from redis import Redis

from app.core.config import settings


class OAuthStateRepository:
    def __init__(self, redis_client: Optional[Redis] = None):
        self.redis = redis_client or Redis.from_url(settings.CELERY_BROKER_URL)
        self.ttl = settings.OAUTH_STATE_TTL_SECONDS

    def _state_key(self, state: str) -> str:
        return f"oauth_state:{state}"

    def create_state(self, provider: str, redirect_uri: str, code_verifier: Optional[str] = None) -> str:
        state = f"oauth_state_{secrets.token_hex(16)}"
        payload = {
            "provider": provider,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.redis.setex(self._state_key(state), self.ttl, json.dumps(payload))
        return state

    def consume_state(self, state: str) -> Optional[dict]:
        key = self._state_key(state)
        data = self.redis.get(key)
        if data is None:
            return None
        self.redis.delete(key)
        return json.loads(data)

    def get_state(self, state: str) -> Optional[dict]:
        data = self.redis.get(self._state_key(state))
        if data is None:
            return None
        return json.loads(data)

    @staticmethod
    def verify_code_challenge(code_verifier: str, code_challenge: str) -> bool:
        expected = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected_b64 = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
        return hmac.compare_digest(expected_b64, code_challenge)


# Singleton
oauth_state_repo = OAuthStateRepository()
