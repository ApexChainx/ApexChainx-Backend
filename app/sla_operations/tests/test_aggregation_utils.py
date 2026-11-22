from app.sla_operations.utils.aggregation import (
    count_by,
    group_by,
    sum_values,
)


# ---------------------------------------------------------------------------
# Fixtures (plain dicts, no DB / core app state)
# ---------------------------------------------------------------------------

EMPTY_DATASET = []

SINGLE_ITEM_DATASET = [
    {"team": "alpha", "priority": "high", "duration": 10},
]

POPULATED_DATASET = [
    {"team": "alpha", "priority": "high", "duration": 10},
    {"team": "alpha", "priority": "low", "duration": 5},
    {"team": "beta", "priority": "high", "duration": 20},
    {"team": "beta", "priority": "high", "duration": 15},
    {"team": "gamma", "priority": "low", "duration": 7},
]


# ---------------------------------------------------------------------------
# Empty dataset
# ---------------------------------------------------------------------------

def test_sum_values_empty_returns_zero():
    assert sum_values(EMPTY_DATASET, "duration") == 0


def test_count_by_empty_returns_empty_dict():
    assert count_by(EMPTY_DATASET, "team") == {}


def test_group_by_empty_returns_empty_dict():
    assert group_by(EMPTY_DATASET, "team") == {}


# ---------------------------------------------------------------------------
# Single-item dataset
# ---------------------------------------------------------------------------

def test_sum_values_single_item():
    assert sum_values(SINGLE_ITEM_DATASET, "duration") == 10


def test_count_by_single_item():
    assert count_by(SINGLE_ITEM_DATASET, "team") == {"alpha": 1}


def test_group_by_single_item():
    assert group_by(SINGLE_ITEM_DATASET, "team") == {
        "alpha": [{"team": "alpha", "priority": "high", "duration": 10}]
    }


# ---------------------------------------------------------------------------
# Populated multi-group dataset
# ---------------------------------------------------------------------------

def test_sum_values_populated():
    assert sum_values(POPULATED_DATASET, "duration") == 57


def test_count_by_populated():
    assert count_by(POPULATED_DATASET, "team") == {
        "alpha": 2,
        "beta": 2,
        "gamma": 1,
    }


def test_count_by_populated_secondary_key():
    assert count_by(POPULATED_DATASET, "priority") == {
        "high": 3,
        "low": 2,
    }


def test_group_by_populated():
    grouped = group_by(POPULATED_DATASET, "team")
    assert set(grouped.keys()) == {"alpha", "beta", "gamma"}
    assert len(grouped["alpha"]) == 2
    assert len(grouped["beta"]) == 2
    assert len(grouped["gamma"]) == 1
    assert grouped["beta"] == [
        {"team": "beta", "priority": "high", "duration": 20},
        {"team": "beta", "priority": "high", "duration": 15},
    ]


def test_group_by_preserves_item_order():
    grouped = group_by(POPULATED_DATASET, "team")
    assert [item["duration"] for item in grouped["alpha"]] == [10, 5]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_sum_values_missing_key_raises_key_error():
    import pytest

    with pytest.raises(KeyError):
        sum_values(SINGLE_ITEM_DATASET, "missing")


def test_count_by_missing_key_raises_key_error():
    import pytest

    with pytest.raises(KeyError):
        count_by(SINGLE_ITEM_DATASET, "missing")


def test_group_by_missing_key_raises_key_error():
    import pytest

    with pytest.raises(KeyError):
        group_by(SINGLE_ITEM_DATASET, "missing")


def test_sum_values_non_numeric_raises_value_error():
    import pytest

    dataset = [{"duration": "not-a-number"}]
    with pytest.raises(ValueError):
        sum_values(dataset, "duration")


def test_sum_values_non_mapping_item_raises_type_error():
    import pytest

    dataset = ["not-a-mapping"]
    with pytest.raises(TypeError):
        sum_values(dataset, "duration")


def test_count_by_non_mapping_item_raises_type_error():
    import pytest

    dataset = ["not-a-mapping"]
    with pytest.raises(TypeError):
        count_by(dataset, "team")


def test_group_by_non_mapping_item_raises_type_error():
    import pytest

    dataset = ["not-a-mapping"]
    with pytest.raises(TypeError):
        group_by(dataset, "team")