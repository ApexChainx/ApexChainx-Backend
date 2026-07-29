import re
from typing import Any, Optional
from app.core.config import settings

STELLAR_SECRET_RE = re.compile(r"^S[A-Za-z0-9]{55}$")
ED25519_KEY_RE = re.compile(r"^[A-Za-z0-9+/=]{88}$")
LONG_KEY_RE = re.compile(r"^[A-Za-z0-9+/=_\-]{32,}$")


def scrub_details(details: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not details:
        return {}
    safe = details.copy()
    for key in list(safe.keys()):
        if key in settings.AUDIT_SENSITIVE_FIELDS:
            safe[key] = "[REDACTED]"
            continue
        value = safe[key]
        if isinstance(value, str):
            if STELLAR_SECRET_RE.match(value):
                safe[key] = "[REDACTED_STELLAR_SECRET]"
            elif ED25519_KEY_RE.match(value):
                safe[key] = "[REDACTED_ED25519]"
            elif len(value) >= 32 and LONG_KEY_RE.match(value):
                safe[key] = "[REDACTED_KEY_MATERIAL]"
    return safe
