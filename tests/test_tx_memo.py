"""Tests for Stellar transaction memo validation (#38)."""

import pytest

from app.services.tx_memo import MEMO_MAX_BYTES, TxMemo


# ── Round-trip golden payloads ──────────────────────────────────────────

GOLDEN_PAYLOADS = [
    ("SLP:OUT001:va3f2c1b9", {"op": "SLP", "agg": "OUT001", "content_hash": "a3f2c1b9"}),
    ("SLR:OUT042:vdeadbeef", {"op": "SLR", "agg": "OUT042", "content_hash": "deadbeef"}),
    ("DSP:dispute-99:v01234567", {"op": "DSP", "agg": "dispute-99", "content_hash": "01234567"}),
    ("RCA:site_alpha:v89abcdef", {"op": "RCA", "agg": "site_alpha", "content_hash": "89abcdef"}),
    ("CFG:critical:v0a1b2c3d", {"op": "CFG", "agg": "critical", "content_hash": "0a1b2c3d"}),
]


@pytest.mark.parametrize("raw,expected", GOLDEN_PAYLOADS)
def test_round_trip_golden(raw, expected):
    """Parse known-good memos and verify they encode back to the same string."""
    memo = TxMemo.parse(raw)
    assert memo.op == expected["op"]
    assert memo.agg == expected["agg"]
    assert memo.content_hash == expected["content_hash"]
    assert memo.encode() == raw


# ── build() determinism ─────────────────────────────────────────────────

def test_build_deterministic():
    """Same inputs always produce the same TxMemo."""
    a = TxMemo.build("SLP", "OUT001", "hello-world")
    b = TxMemo.build("SLP", "OUT001", "hello-world")
    assert a.encode() == b.encode()
    assert a.content_hash == b.content_hash


def test_build_different_payload_different_hash():
    """Different payloads produce different content hashes."""
    a = TxMemo.build("SLP", "OUT001", "hello-world")
    b = TxMemo.build("SLP", "OUT001", "hello-world-2")
    assert a.content_hash != b.content_hash


# ── Byte limit enforcement ──────────────────────────────────────────────

def test_rejects_exceeds_28_bytes():
    """Memo longer than 28 bytes must raise ValueError with 422-friendly message."""
    too_long = "SLP:VERY_LONG_AGGREG:x12345678"  # 32+ bytes
    with pytest.raises(ValueError, match="exceeds"):
        TxMemo.parse(too_long)


def test_build_rejects_result_over_28_bytes():
    """build() should reject if the constructed memo exceeds 28 bytes."""
    long_agg = "X" * 17  # 17-char agg pushes total past 28
    with pytest.raises(ValueError, match="exceeds"):
        TxMemo.build("SLP", long_agg, "data")


# ── Op code whitelist ───────────────────────────────────────────────────

def test_rejects_unknown_op_code():
    """Unrecognized operation codes must be rejected."""
    with pytest.raises(ValueError, match="Unknown operation code"):
        TxMemo(op="BAD", agg="OUT001", content_hash="a3f2c1b9")


def test_op_code_case_insensitive():
    """Op codes should be normalized to uppercase."""
    memo = TxMemo.parse("slp:OUT001:va3f2c1b9")
    assert memo.op == "SLP"


# ── Content hash validation ─────────────────────────────────────────────

def test_rejects_non_hex_content_hash():
    """content_hash with non-hex chars must be rejected."""
    with pytest.raises(ValueError, match="content_hash"):
        TxMemo(agg="OUT001", op="SLP", content_hash="xyz12345")


def test_rejects_wrong_length_hash():
    """content_hash must be exactly 8 characters."""
    with pytest.raises(ValueError, match="content_hash"):
        TxMemo(agg="OUT001", op="SLP", content_hash="abc123")


# ── Malformed format ────────────────────────────────────────────────────

def test_rejects_missing_version_prefix():
    """Memo without 'v' prefix before hash must be rejected."""
    with pytest.raises(ValueError, match="Invalid memo format"):
        TxMemo.parse("SLP:OUT001:a3f2c1b9")


def test_rejects_missing_colons():
    """Memo without proper colon delimiters must be rejected."""
    with pytest.raises(ValueError, match="Invalid memo format"):
        TxMemo.parse("SLP-OUT001-va3f2c1b9")


def test_rejects_empty_string():
    """Empty memo string must be rejected."""
    with pytest.raises(ValueError, match="Invalid memo format"):
        TxMemo.parse("")


# ── is_suspicious() audit helper ────────────────────────────────────────

def test_is_suspicious_clean_memo():
    """Well-formed memos should not be flagged suspicious."""
    assert not TxMemo.is_suspicious("SLP:OUT001:va3f2c1b9")


def test_is_suspicious_bad_memo():
    """Malformed memos should be flagged suspicious."""
    assert TxMemo.is_suspicious("raw-uuid-without-structure")


def test_is_suspicious_empty():
    """Empty memos are suspicious."""
    assert TxMemo.is_suspicious("")


# ── byte_length() ───────────────────────────────────────────────────────

def test_byte_length_matches_encoded():
    """byte_length() should match len(encode().encode('utf-8'))."""
    memo = TxMemo.parse("SLP:OUT001:va3f2c1b9")
    assert memo.byte_length() == len(memo.encode().encode("utf-8"))
    assert memo.byte_length() <= MEMO_MAX_BYTES


# ── All golden payloads within byte limit ───────────────────────────────

def test_all_goldens_within_byte_limit():
    """Every golden payload must fit within the Stellar 28-byte limit."""
    for raw, _ in GOLDEN_PAYLOADS:
        memo = TxMemo.parse(raw)
        assert memo.byte_length() <= MEMO_MAX_BYTES, f"{raw} is {memo.byte_length()} bytes"
