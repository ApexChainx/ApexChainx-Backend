"""Unit tests for the SLA operations workspace result/error helpers.

These tests are intentionally isolated from core application state and only
exercise the pure helpers in ``app.sla_operations.utils.result_types``.
"""

from __future__ import annotations

import pytest

from app.sla_operations.utils.result_types import (
    ErrorCode,
    Result,
    failure,
    is_failure,
    is_success,
    make_error,
    success,
    unwrap,
    unwrap_or,
)


class TestSuccessConstruction:
    def test_success_holds_value(self) -> None:
        result = success(42)
        assert result.ok is True
        assert result.value == 42
        assert result.error is None

    def test_success_with_none_value(self) -> None:
        result = success(None)
        assert result.ok is True
        assert result.value is None
        assert result.error is None

    def test_success_with_complex_value(self) -> None:
        payload = {"a": 1, "b": [2, 3]}
        result = success(payload)
        assert result.ok is True
        assert result.value == payload


class TestFailureConstruction:
    def test_failure_holds_error(self) -> None:
        result = failure(ErrorCode.NOT_FOUND, "missing")
        assert result.ok is False
        assert result.value is None
        assert result.error is not None
        assert result.error.code == ErrorCode.NOT_FOUND
        assert result.error.message == "missing"

    def test_failure_with_details(self) -> None:
        result = failure(
            ErrorCode.VALIDATION_ERROR,
            "bad input",
            details={"field": "name"},
        )
        assert result.error is not None
        assert result.error.details == {"field": "name"}

    def test_failure_accepts_string_code(self) -> None:
        result = failure("custom_code", "something happened")
        assert result.error is not None
        assert result.error.code == "custom_code"

    def test_failure_rejects_empty_message(self) -> None:
        with pytest.raises(ValueError):
            failure(ErrorCode.INTERNAL_ERROR, "")

    def test_failure_rejects_whitespace_message(self) -> None:
        with pytest.raises(ValueError):
            failure(ErrorCode.INTERNAL_ERROR, "   ")

    def test_failure_rejects_empty_code(self) -> None:
        with pytest.raises(ValueError):
            failure("", "message")

    def test_failure_rejects_none_code(self) -> None:
        with pytest.raises(ValueError):
            failure(None, "message")  # type: ignore[arg-type]


class TestMakeError:
    def test_make_error_builds_error(self) -> None:
        err = make_error(ErrorCode.CONFLICT, "already exists")
        assert err.code == ErrorCode.CONFLICT
        assert err.message == "already exists"
        assert err.details is None

    def test_make_error_invalid_message(self) -> None:
        with pytest.raises(ValueError):
            make_error(ErrorCode.CONFLICT, "")


class TestPredicates:
    def test_is_success_and_is_failure(self) -> None:
        ok = success(1)
        bad = failure(ErrorCode.NOT_FOUND, "nope")
        assert is_success(ok) is True
        assert is_failure(ok) is False
        assert is_success(bad) is False
        assert is_failure(bad) is True


class TestUnwrap:
    def test_unwrap_success_returns_value(self) -> None:
        assert unwrap(success("hello")) == "hello"

    def test_unwrap_failure_raises(self) -> None:
        with pytest.raises(ValueError):
            unwrap(failure(ErrorCode.NOT_FOUND, "missing"))

    def test_unwrap_failure_raises_custom_exception(self) -> None:
        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            unwrap(failure(ErrorCode.NOT_FOUND, "missing"), exc_type=Boom)


class TestUnwrapOr:
    def test_unwrap_or_success(self) -> None:
        assert unwrap_or(success(5), 0) == 5

    def test_unwrap_or_failure_returns_default(self) -> None:
        assert unwrap_or(failure(ErrorCode.NOT_FOUND, "x"), 0) == 0

    def test_unwrap_or_failure_none_default(self) -> None:
        assert unwrap_or(failure(ErrorCode.NOT_FOUND, "x"), None) is None


class TestResultType:
    def test_result_is_generic_container(self) -> None:
        result: Result[int] = success(3)
        assert result.value == 3

    def test_result_equality(self) -> None:
        assert success(1) == success(1)
        assert success(1) != success(2)
        assert failure(ErrorCode.NOT_FOUND, "x") == failure(
            ErrorCode.NOT_FOUND, "x"
        )