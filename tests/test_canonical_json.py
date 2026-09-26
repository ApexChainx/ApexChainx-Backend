from app.services.formatters import canonical_json


def test_canonical_json_is_stable_and_hashable():
    payload = {"z": 1, "a": [2, {"b": True, "c": None}], "nested": {"d": "x"}}
    expected = '{"a":[2,{"b":true,"c":null}],"nested":{"d":"x"},"z":1}'

    assert canonical_json(payload) == expected
    assert canonical_json(payload) == canonical_json({"nested": {"d": "x"}, "a": [2, {"b": True, "c": None}], "z": 1})
