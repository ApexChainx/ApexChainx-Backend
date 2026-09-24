"""Full-password fingerprint for credential stuffing detection,
replacing a 4-character prefix hash that collapses distinct attempts.
"""
import hashlib


def fingerprint_password(password: str) -> str:
    normalized = password.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
