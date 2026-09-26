"""Keyed/salted hashing for new API keys and webhook secrets, replacing
unsalted single-round SHA-256 that's vulnerable to offline dictionary
attacks against a database dump.
"""
import hashlib
import hmac
import os


def hash_token_keyed(token: str, server_secret: bytes, salt: bytes = None) -> str:
    salt = salt or os.urandom(16)
    digest = hmac.new(server_secret, salt + token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{salt.hex()}:{digest}"


def verify_token_keyed(token: str, server_secret: bytes, stored: str) -> bool:
    salt_hex, _, expected_digest = stored.partition(":")
    salt = bytes.fromhex(salt_hex)
    candidate = hmac.new(server_secret, salt + token.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(candidate, expected_digest)
