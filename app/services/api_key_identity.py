"""Full-length API key identity: a random key id plus a SHA-256 hash of
the raw token for lookup, replacing a short-hash marker prone to
collisions across users.
"""
import hashlib
import secrets


def generate_api_key_id() -> str:
    return secrets.token_hex(16)


def hash_api_key_for_lookup(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
