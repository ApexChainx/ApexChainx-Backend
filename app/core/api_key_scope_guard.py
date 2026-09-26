"""Enforces an API key's declared scope against the route's required
scope, instead of only authenticating the key.
"""
from fastapi import Depends, HTTPException, status


def require_api_key_scope(required_scope: str):
    def _check(current_key=Depends(lambda: None)):
        if current_key is None or required_scope not in getattr(current_key, "scopes", []):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient API key scope")
        return current_key

    return _check
