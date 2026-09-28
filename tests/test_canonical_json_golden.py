"""Golden fixture: canonical_json must be byte-stable — Issue #570.

``canonical_json`` (``app/services/formatters.py``) feeds the audit hash
chain: ``AuditLogService._compute_entry_hash`` SHA-256s exactly these bytes.
If the serialization ever drifts — key order, separators, unicode escaping,
float/int rendering — every newly computed hash stops matching what a
previously deployed environment would have produced for the same payload,
silently forking the chain (``verify_chain`` would then report every entry
written by the newer build as ``first_bad_id``).

This test locks the exact serialized bytes for a fixture that covers every
axis the function is trusted on:

- non-ASCII keys AND values (keys sort by code point; ``ensure_ascii=False``
  must keep them literal UTF-8, never ``\\uXXXX``-escaped);
- nested containers (dicts recurse with sorted keys, lists keep order);
- mixed-type list elements (ints, floats, bools, null, nested structures);
- int vs float distinction (``1`` must stay ``1``, never ``1.0``);
- the ``default=str`` fallback for non-JSON types;
- empty containers and the empty string.

The golden bytes live inline, right next to the SHA-256 of the full fixture,
so a failure names the drift directly instead of failing somewhere deep in a
hash-chain test.
"""

import hashlib

from app.services.formatters import canonical_json

# Deliberately unordered: the fixture must survive being rebuilt in any
# key order, which is exactly what sort_keys guarantees.
FIXTURE = {
    "emoji_key_🚀": {"z_last": [1, 2, 3], "a_first": None},
    "café_ünïcode": "vàlue—with—dashes",
    "empty": {"dict": {}, "list": [], "str": ""},
    "list_mixed": [
        42,
        -3,
        0,
        3.14,
        True,
        False,
        None,
        "unicode-in-list-µ",
        {"nested": {"deep": [1, [2, [3]]]}},
    ],
    "ascii": "plain",
    "num_int": 1,
    "num_float": 1.0,
    "bool": True,
}

# Golden bytes: sorted keys (code-point order), no whitespace, literal UTF-8.
GOLDEN = (
    '{"ascii":"plain",'
    '"bool":true,'
    '"café_ünïcode":"vàlue—with—dashes",'
    '"emoji_key_🚀":{"a_first":null,"z_last":[1,2,3]},'
    '"empty":{"dict":{},"list":[],"str":""},'
    '"list_mixed":[42,-3,0,3.14,true,false,null,"unicode-in-list-µ",{"nested":{"deep":[1,[2,[3]]]}}],'
    '"num_float":1.0,'
    '"num_int":1}'
)

# SHA-256 of the golden bytes, so a drift that still yields valid JSON still
# fails here and the diff names the byte change.
GOLDEN_SHA256 = "cc347f4e38c3b658b69b9d18e6167839781c8cde3296ee0e5928819c36caa440"


def test_golden_bytes_exact():
    """The fixture serializes to the exact golden bytes, byte for byte."""
    assert canonical_json(FIXTURE) == GOLDEN


def test_golden_sha256():
    """The SHA-256 of the golden bytes is unchanged."""
    assert hashlib.sha256(canonical_json(FIXTURE).encode("utf-8")).hexdigest() == GOLDEN_SHA256


def test_key_order_independence():
    """Rebuilding the payload in a different key order must not change the bytes."""
    reordered = {
        "num_int": 1,
        "bool": True,
        "num_float": 1.0,
        "ascii": "plain",
        "list_mixed": FIXTURE["list_mixed"],
        "empty": FIXTURE["empty"],
        "café_ünïcode": "vàlue—with—dashes",
        "emoji_key_🚀": FIXTURE["emoji_key_🚀"],
    }
    assert canonical_json(reordered) == canonical_json(FIXTURE) == GOLDEN


def test_non_ascii_is_not_escaped():
    """ensure_ascii=False: unicode must appear literally, never \\u-escaped."""
    out = canonical_json({"k": "µç"})
    assert out == '{"k":"µç"}'
    assert "\\u" not in out


def test_separators_are_compact():
    """No whitespace: (',', ':') separators, not the json-module defaults."""
    out = canonical_json({"a": 1, "b": [1, 2]})
    assert out == '{"a":1,"b":[1,2]}'
    assert " " not in out


def test_int_float_distinction():
    """1 and 1.0 must render differently — hashes would otherwise collide."""
    assert canonical_json({"n": 1}) == '{"n":1}'
    assert canonical_json({"n": 1.0}) == '{"n":1.0}'
    assert canonical_json({"n": 1}) != canonical_json({"n": 1.0})


def test_list_order_is_preserved():
    """sort_keys sorts object keys; array order must stay untouched."""
    assert canonical_json({"k": [3, 1, 2]}) == '{"k":[3,1,2]}'


def test_default_str_fallback():
    """Non-JSON types go through str() deterministically."""
    assert canonical_json({"d": object()}).startswith('{"d":"<object object at')
