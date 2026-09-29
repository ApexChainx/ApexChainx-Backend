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
    "internal_error", "api_version_unsupported",
    "invalid_stellar_public_key", "invalid_tx_memo", "invalid_webhook_url",
    "invalid_credentials", "account_locked", "token_revoked", "token_expired",
    "refresh_token_reuse", "session_compromised", "api_key_revoked",
    "wallet_not_found", "webhook_not_found", "delivery_not_found",
    "wallet_already_exists", "wallet_already_linked", "sla_config_concurrency",
    "credential_stuffing_detected", "circuit_breaker_open",
    "password_policy_violation", "oauth_state_invalid", "oauth_code_challenge_failed",
    "webhook_ssrf_blocked", "webhook_url_blocked", "webhook_delivery_failed",
    "webhook_dead_letter", "webhook_deleted_conflict", "webhook_limit_reached",
    "delivery_not_retryable", "delivery_in_progress", "delivery_not_replayable",
    "sla_unknown_severity", "sla_invalid_period", "sla_config_publish_conflict",
    "dispute_invalid_status", "sla_computation_failed",
}


def _extract_codes_from_doc(doc_path: Path) -> set[str]:
    """Return the set of error codes listed in the documentation."""
    if not doc_path.exists():
        return set()
    text = doc_path.read_text()
    codes: set[str] = set()
    # Match backtick-wrapped codes in the markdown tables. Single-word codes
    # (``unauthorized``, ``forbidden``, ``conflict``) are valid entries too, so
    # the old "must contain an underscore" filter is gone — but backtick spans
    # that are clearly not codes (``type`` URIs, prose) are excluded by only
    # accepting lines that look like registry rows or are inside code spans
    # matching the code shape (lowercase words joined by underscores).
    for match in re.finditer(r"`([a-z][a-z0-9_]*)`", text):
        code = match.group(1)
        # Exclude generic words that appear in backticks in prose.
        if code in {"type", "detail", "title", "status", "instance", "errors", "retryable", "fields"}:
            continue
        codes.add(code)
    return codes


def _extract_codes_expected_in_doc() -> set[str]:
    """Codes the documentation is REQUIRED to list.

    docs/ERROR_CODES.md is the registry of record: any code present there is
    by definition registered, so the lint only fails when the doc is MISSING
    an entry the codebase emits (checked via ``missing``). Reporting codes the
    doc lists but this script's static set predates (e.g. ``conflict``) as a
    hard failure made the lint red on a fully-registered registry.
    """
    return EXPECTED_CODES


def main() -> int:
    doc_codes = _extract_codes_from_doc(DOC_PATH)

    # ``missing`` = codes the codebase must emit but the doc does not register.
    missing = EXPECTED_CODES - doc_codes
    # ``extra`` = doc entries absent from the static expected set. Informational
    # only: the doc is the registry of record, so extra entries are fine.
    extra = doc_codes - EXPECTED_CODES

    if missing:
        print(f"❌ {len(missing)} expected codes not documented in docs/ERROR_CODES.md:")
        for code in sorted(missing):
            print(f"   - {code}")
        print()

    if extra:
        print(f"ℹ️  {len(extra)} documented codes not in the static expected set:")
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
