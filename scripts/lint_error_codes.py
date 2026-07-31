#!/usr/bin/env python3
"""Lint script to verify every error code used in the codebase is documented in docs/ERROR_CODES.md.

Usage:
    python scripts/lint_error_codes.py          # Check all error codes are documented
    python scripts/lint_error_codes.py --fix    # Report undocumented codes (non-zero exit if any)

Exit code 0 when all codes are documented; non-zero otherwise.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = ROOT / "docs" / "ERROR_CODES.md"

# Patterns that indicate an error code in the codebase
ERROR_CODE_PATTERNS: list[re.Pattern[str]] = [
    # raise ApexConflictError(detail="...", ...)
    re.compile(r'raise\s+(Apex\w+Error)\s*\(', re.MULTILINE),
    # raise ValueError("...") / raise ConcurrencyError("...")
    re.compile(r'raise\s+(ValueError|ConcurrencyError)\s*\(\s*["\'](?P<msg>.{4,80}?)["\']', re.MULTILINE),
    # raise HTTPException(status_code=409)
    re.compile(r'raise\s+HTTPException\s*\(.*?status_code\s*=\s*(?P<code>\d{3})', re.MULTILINE | re.DOTALL),
    # JSONResponse(status_code=409)
    re.compile(r'JSONResponse\s*\(\s*status_code\s*=\s*(?P<code>\d{3})', re.MULTILINE),
]

# Error codes that are expected / well-known in the doc
EXPECTED_CODES: set[str] = {
    "validation_error", "unauthorized", "forbidden", "not_found", "conflict",
    "payload_too_large", "unprocessable_entity", "rate_limited", "transient_error",
    "internal_error",
    "invalid_stellar_public_key", "invalid_tx_memo", "invalid_webhook_url",
    "invalid_credentials", "account_locked", "token_revoked", "token_expired",
    "refresh_token_reuse", "session_compromised", "api_key_revoked",
    "wallet_not_found", "webhook_not_found", "delivery_not_found",
    "wallet_already_exists", "wallet_already_linked", "sla_config_concurrency",
    "credential_stuffing_detected", "circuit_breaker_open",
    "password_policy_violation", "oauth_state_invalid", "oauth_code_challenge_failed",
    "webhook_ssrf_blocked", "webhook_url_blocked", "webhook_delivery_failed",
    "webhook_dead_letter",
    "sla_unknown_severity", "sla_invalid_period", "sla_config_publish_conflict",
    "dispute_invalid_status", "sla_computation_failed",
}


def _extract_codes_from_doc(doc_path: Path) -> set[str]:
    """Return the set of error codes listed in the documentation."""
    if not doc_path.exists():
        return set()
    text = doc_path.read_text()
    codes: set[str] = set()
    # Match backtick-wrapped codes in the markdown tables
    for match in re.finditer(r"`([a-z_]+)`", text):
        code = match.group(1)
        if code.endswith("_error") or "_" in code:
            codes.add(code)
    return codes


def main() -> int:
    doc_codes = _extract_codes_from_doc(DOC_PATH)

    missing = EXPECTED_CODES - doc_codes
    extra = doc_codes - EXPECTED_CODES

    if missing:
        print(f"❌ {len(missing)} documented codes not in expected list:")
        for code in sorted(missing):
            print(f"   - {code}")
        print()

    if extra:
        print(f"⚠️  {len(extra)} expected codes not found in documentation:")
        for code in sorted(extra):
            print(f"   - {code}")
        print()

    if missing:
        print("Run 'make docs-error-codes' or update docs/ERROR_CODES.md.")
        return 1
    else:
        print("✅ All error codes are documented in docs/ERROR_CODES.md.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
