"""Implements the OAuth provider code exchange, replacing the 501 stub
that made the documented SSO login flow unusable.
"""
from typing import Any, Dict


class OAuthExchangeError(Exception):
    pass


def exchange_code_for_session(provider_token_url: str, code: str, state: str,
                               expected_state: str, http_client) -> Dict[str, Any]:
    if state != expected_state:
        raise OAuthExchangeError("OAuth state mismatch")

    response = http_client.post(provider_token_url, data={"code": code, "grant_type": "authorization_code"})
    if response.status_code != 200:
        raise OAuthExchangeError(f"Provider token exchange failed: {response.status_code}")
    return response.json()
