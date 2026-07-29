"""Stellar transaction memo validation for SLA settlement payments (#38).

Memo format: <op>:<agg>:v<hash>
- op: whitelisted operation code (e.g. SLP=settle-penalty, SLR=settle-reward)
- agg: aggregation key (e.g. outage_id prefix)
- v<hash>: versioned content hash (8 hex chars from SHA-256)

Max length: 28 bytes (Stellar memo limit).

Example: SLP:OUT001:va3f2c1b9
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Whitelisted operation codes
VALID_OP_CODES: frozenset[str] = frozenset(
    {
        "SLP",  # settle-penalty
        "SLR",  # settle-reward
        "DSP",  # dispute-proposed
        "DSR",  # dispute-resolved
        "RCA",  # root-cause-analysis
        "CFG",  # config-publish
    }
)

MEMO_MAX_BYTES = 28
MEMO_PATTERN = re.compile(r"^([A-Za-z]{2,3}):([A-Za-z0-9_-]{1,16}):v([a-f0-9]{8})$")


class TxMemo(BaseModel):
    """Validated Stellar transaction memo for SLA-related operations.

    Fields:
        op: Operation code (2-3 uppercase letters, must be whitelisted)
        agg: Aggregation key (alphanumeric + _-, max 16 chars)
        content_hash: 8-char hex prefix of SHA-256 hash
    """

    op: str = Field(..., min_length=2, max_length=3, description="Operation code (whitelisted)")
    agg: str = Field(..., min_length=1, max_length=16, description="Aggregation key")
    content_hash: str = Field(..., min_length=8, max_length=8, description="8-char hex prefix of SHA-256")

    @field_validator("op")
    @classmethod
    def op_must_be_whitelisted(cls, v: str) -> str:
        upper = v.upper()
        if upper not in VALID_OP_CODES:
            raise ValueError(f"Unknown operation code '{v}'. Must be one of: {sorted(VALID_OP_CODES)}")
        return upper

    @field_validator("content_hash")
    @classmethod
    def hash_must_be_hex(cls, v: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{8}", v.lower()):
            raise ValueError("content_hash must be exactly 8 lowercase hex characters")
        return v.lower()

    def encode(self) -> str:
        """Encode to wire format: <op>:<agg>:v<hash>"""
        return f"{self.op}:{self.agg}:v{self.content_hash}"

    def byte_length(self) -> int:
        """Number of UTF-8 bytes of the encoded memo."""
        return len(self.encode().encode("utf-8"))

    @classmethod
    def parse(cls, raw: str) -> TxMemo:
        """Parse a raw memo string into a validated TxMemo.

        Raises:
            ValueError: If the format is invalid.
        """
        if len(raw.encode("utf-8")) > MEMO_MAX_BYTES:
            raise ValueError(f"Memo exceeds {MEMO_MAX_BYTES} bytes (got {len(raw.encode('utf-8'))})")

        match = MEMO_PATTERN.match(raw)
        if not match:
            raise ValueError(
                f"Invalid memo format '{raw}'. Expected: <OP>:<agg>:v<hash8> " f"(e.g. SLP:OUT001:va3f2c1b9)"
            )

        op, agg, content_hash = match.groups()
        return cls(op=op, agg=agg, content_hash=content_hash)

    @classmethod
    def build(
        cls,
        op: str,
        agg: str,
        payload: str,
        algorithm: Literal["sha256"] = "sha256",
    ) -> TxMemo:
        """Build a TxMemo from constituent parts.

        Args:
            op: Operation code (e.g. 'SLP')
            agg: Aggregation key (e.g. outage_id truncated to 16 chars)
            payload: The full payload to hash
            algorithm: Hashing algorithm (default: sha256)

        Returns:
            Validated TxMemo instance.

        Raises:
            ValueError: If any field fails validation or exceeds 28 bytes.
        """
        if algorithm == "sha256":
            full_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        else:
            raise ValueError(f"Unsupported hash algorithm: {algorithm}")

        content_hash = full_hash[:8]
        memo = cls(op=op, agg=agg[:16], content_hash=content_hash)

        if memo.byte_length() > MEMO_MAX_BYTES:
            raise ValueError(
                f"Encoded memo '{memo.encode()}' is {memo.byte_length()} bytes, " f"exceeds {MEMO_MAX_BYTES} byte limit"
            )

        return memo

    @classmethod
    def is_suspicious(cls, raw: str) -> bool:
        """Check if a raw memo looks suspicious (malformed, wrong length, bad op code).

        Returns True if the memo does NOT parse cleanly — used by audit logging
        to flag potentially mis-encoded or privacy-leaking memos.
        """
        try:
            cls.parse(raw)
            return False
        except ValueError:
            return True
