"""Full-length session token identifiers, replacing an 8-hex-char
truncation whose ~32 bits of space risks birthday collisions at scale.
"""
import hashlib
import secrets

DISPLAY_ID_LENGTH = 8


def generate_session_token() -> str:
    return secrets.token_hex(32)


def session_lookup_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def display_id(token: str) -> str:
    return token[:DISPLAY_ID_LENGTH]
