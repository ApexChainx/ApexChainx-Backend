"""Golden fixtures pinning ``canonical_json`` byte-stability — Issue #570.

``canonical_json`` feeds the audit hash chain (``app/services/audit_log.py``
hashes every entry over its canonical serialization) and webhook event
storage, so a silent drift in its output would (a) fork the audit chain —
entries written after the change would no longer verify against pre-change
hashes — and (b) store webhook payloads in a different shape. The function's
whole point is to be *stable across environments*, so the serialized bytes are
pinned exactly: any change to the output — key order, separators, unicode
escaping, number formatting — fails these tests before it can land.

The plain-value fixtures cover the axes the old stability test didn't:

- non-ASCII (``ensure_ascii=False`` means CJK/accents/emoji must survive
  byte-for-byte as raw UTF-8, not ``\\uXXXX`` escapes),
- key ordering (nested ``b``/``a`` keys must come out sorted),
- separators (no whitespace: ``, ``/``: `` would drift the bytes),
- booleans/nulls/large ints/floats (Python's JSON number spelling),
- deeply nested structures (recursion must not reorder arrays),
- an empty value, and
- key-collision-free equivalence: the same logical payload in different
  insertion orders must serialize to identical bytes.
"""

import pytest

from app.services.formatters import canonical_json


# --- Audit-chain-shaped golden fixtures ------------------------------------
#
# These mirror the dict canonical_json actually serializes in production:
# an audit-chain entry (app/services/audit_log.py::_compute_entry_hash) and a
# webhook registration payload (app/api/v1/endpoints/webhooks.py). Pinning the
# exact serialized bytes of these shapes means any change to the serializer is
# caught before it can fork the chain.

AUDIT_ENTRY_GOLDEN = {
    "prev_hash": "3a" * 32,
    "event_type": "sla_settlement_initiated",
    "details": {
        "outage_id": "OUT-2026-001",
        "amount": 125_000,
        "asset": "XLM",
        "memo": "SLP:OUT-2026-001:va3f2c1b9",
    },
    "correlation_id": "corr-95c1a2",
    "created_at": "2026-09-29T12:00:00+00:00",
}

AUDIT_ENTRY_GOLDEN_BYTES = (
    b'{"correlation_id":"corr-95c1a2",'
    b'"created_at":"2026-09-29T12:00:00+00:00",'
    b'"details":{"amount":125000,"asset":"XLM","memo":"SLP:OUT-2026-001:va3f2c1b9","outage_id":"OUT-2026-001"},'
    b'"event_type":"sla_settlement_initiated",'
    b'"prev_hash":"3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a3a"}'
)

WEBHOOK_EVENTS_GOLDEN = ["sla.result.computed", "payment.confirmed", "outage.detected"]

# Object keys are sorted; array element order is preserved as inserted.
WEBHOOK_EVENTS_GOLDEN_BYTES = (
    b'["sla.result.computed","payment.confirmed","outage.detected"]'
)


def test_audit_entry_serializes_to_the_pinned_bytes():
    """The audit-chain entry shape serializes to exactly these bytes."""
    assert canonical_json(AUDIT_ENTRY_GOLDEN).encode() == AUDIT_ENTRY_GOLDEN_BYTES


def test_webhook_event_list_serializes_to_the_pinned_bytes():
    """The webhook events column stores exactly these bytes."""
    assert canonical_json(WEBHOOK_EVENTS_GOLDEN).encode() == WEBHOOK_EVENTS_GOLDEN_BYTES


# --- Non-ASCII / unicode fixtures (issue #570) ------------------------------


def test_non_ascii_is_serialized_as_raw_utf8_not_escaped():
    """ensure_ascii=False is a byte-level contract: no \\uXXXX escapes."""
    value = {"site": "東京サイト", "severity": "critique", "emoji": "🛰️"}
    assert canonical_json(value) == '{"emoji":"🛰️","severity":"critique","site":"東京サイト"}'


def test_non_ascii_equivalence_across_key_orders():
    """The same unicode payload re-inserted serializes to identical bytes."""
    a = {"site": "東京", "note": "café"}
    b = {"note": "café", "site": "東京"}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a).encode() == canonical_json(b).encode()


# --- Structural fixtures -----------------------------------------------------


def test_nested_keys_are_sorted_at_every_depth():
    value = {"b": {"y": 2, "a": 1}, "a": {"z": [ {"d": 4, "c": 3} ]}}
    assert canonical_json(value) == '{"a":{"z":[{"c":3,"d":4}]},"b":{"a":1,"y":2}}'


def test_no_whitespace_between_tokens():
    value = {"k": [1, 2, {"x": "y"}]}
    assert canonical_json(value) == '{"k":[1,2,{"x":"y"}]}'


def test_primitives_and_numbers_are_pinned():
    assert canonical_json(True) == "true"
    assert canonical_json(None) == "null"
    assert canonical_json(10**18) == "1000000000000000000"
    assert canonical_json(1.5) == "1.5"


def test_deeply_nested_array_order_is_preserved():
    value = {"l3": [{"l2": [{"l1": {"newest": 1, "oldest": 0}}]}]}
    assert canonical_json(value) == '{"l3":[{"l2":[{"l1":{"newest":1,"oldest":0}}]}]}'


def test_empty_structures():
    assert canonical_json({}) == "{}"
    assert canonical_json([]) == "[]"
    assert canonical_json({"empty": {}}) == '{"empty":{}}'


def test_hash_friendly_round_trip():
    """A hash over the canonical bytes is reproducible."""
    import hashlib

    payload = {"b": 2, "a": {"d": [4, 3], "c": "ünïcode"}}
    raw = canonical_json(payload)
    assert hashlib.sha256(raw.encode()).hexdigest() == hashlib.sha256(
        canonical_json({"a": {"c": "ünïcode", "d": [4, 3]}, "b": 2}).encode()
    ).hexdigest()


# --- The original stability assertions (kept verbatim) ----------------------


def test_canonical_json_is_stable_and_hashable():
    payload = {"z": 1, "a": [2, {"b": True, "c": None}], "nested": {"d": "x"}}
    expected = '{"a":[2,{"b":true,"c":null}],"nested":{"d":"x"},"z":1}'

    assert canonical_json(payload) == expected
    assert canonical_json(payload) == canonical_json({"nested": {"d": "x"}, "a": [2, {"b": True, "c": None}], "z": 1})


# --- Environment-drift guard (issue #570) -----------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"a": 1}, b'{"a":1}'),
        ({"site": "東京"}, '{"site":"東京"}'.encode()),
        ([None, True, False], b"[null,true,false]"),
        ({"k": "s"}, b'{"k":"s"}'),
    ],
)
def test_canonical_bytes_are_environment_independent(value, expected):
    """Recompute the serialization rather than trusting a cached string.

    Guards against interpreter/build differences (e.g. a re-introduced
    ``ensure_ascii`` default, or locale-dependent number formatting) changing
    the bytes between environments.
    """
    assert canonical_json(value).encode() == expected
