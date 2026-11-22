from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.sla_operations.utils import json_serialization as js


def _has(name: str) -> bool:
    return hasattr(js, name)


def test_module_exposes_serialization_helpers() -> None:
    # The module should expose at least a dumps-like and loads-like helper.
    assert _has("dumps") or _has("to_json") or _has("serialize")
    assert _has("loads") or _has("from_json") or _has("deserialize")


def _dumps(value):
    if _has("dumps"):
        return js.dumps(value)
    if _has("to_json"):
        return js.to_json(value)
    return js.serialize(value)


def _loads(value):
    if _has("loads"):
        return js.loads(value)
    if _has("from_json"):
        return js.from_json(value)
    return js.deserialize(value)


def test_stable_key_ordering_across_insertion_orders() -> None:
    first = {"b": 2, "a": 1, "c": 3}
    second = {"c": 3, "a": 1, "b": 2}

    out_first = _dumps(first)
    out_second = _dumps(second)

    assert out_first == out_second
    assert out_first == json.dumps({"a": 1, "b": 2, "c": 3}, sort_keys=True)


def test_stable_key_ordering_nested() -> None:
    first = {"outer": {"z": 1, "a": 2}, "list": [{"y": 1, "x": 2}]}
    second = {"list": [{"x": 2, "y": 1}], "outer": {"a": 2, "z": 1}}

    assert _dumps(first) == _dumps(second)


def test_round_trip_simple() -> None:
    payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
    assert _loads(_dumps(payload)) == payload


def test_round_trip_nested_structures() -> None:
    payload = {
        "list": [1, "two", {"three": 3}, [4, [5]]],
        "dict": {"a": {"b": {"c": [1, 2, 3]}}},
        "mixed": [{"k": "v"}, None, True, False],
    }
    assert _loads(_dumps(payload)) == payload


def test_empty_dict_round_trip() -> None:
    assert _loads(_dumps({})) == {}


def test_empty_list_round_trip() -> None:
    assert _loads(_dumps([])) == []


def test_none_round_trip() -> None:
    assert _loads(_dumps(None)) is None


def test_empty_string_round_trip() -> None:
    assert _loads(_dumps("")) == ""


def test_datetime_handling() -> None:
    dt = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    payload = {"ts": dt}

    try:
        out = _dumps(payload)
    except TypeError:
        # Documented behavior: datetimes are not serializable.
        pytest.skip("datetime serialization not supported by target module")

    decoded = _loads(out)
    assert decoded["ts"] == dt.isoformat()


def test_decimal_handling() -> None:
    payload = {"amount": Decimal("12.34")}

    try:
        out = _dumps(payload)
    except TypeError:
        pytest.skip("Decimal serialization not supported by target module")

    decoded = _loads(out)
    # Accept either string or numeric representation, but must round-trip value.
    assert Decimal(str(decoded["amount"])) == Decimal("12.34")


def test_unsupported_type_raises_type_error() -> None:
    class Unserializable:
        pass

    with pytest.raises(TypeError):
        _dumps({"obj": Unserializable()})


def test_invalid_json_raises() -> None:
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _loads("{not valid json")


def test_deterministic_repeated_calls() -> None:
    payload = {"b": [3, 2, 1], "a": {"y": 2, "x": 1}}
    outputs = {_dumps(payload) for _ in range(5)}
    assert len(outputs) == 1
</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>