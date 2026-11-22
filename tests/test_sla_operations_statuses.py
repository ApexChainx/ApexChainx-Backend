"""Unit tests for SLA operations status normalization utilities.

These tests are intentionally isolated: they do not import application
fixtures, database state, or any core modules beyond the utility module
under test.
"""

from __future__ import annotations

import pytest

from app.sla_operations.utils.statuses import (
    normalize_generic_status,
    normalize_outage_status,
    normalize_payment_status,
    normalize_webhook_status,
)


class TestNormalizeOutageStatus:
    """Tests for normalize_outage_status."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("open", "open"),
            ("investigating", "investigating"),
            ("identified", "identified"),
            ("monitoring", "monitoring"),
            ("resolved", "resolved"),
            ("closed", "closed"),
        ],
    )
    def test_canonical_values(self, value: str, expected: str) -> None:
        assert normalize_outage_status(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("OPEN", "open"),
            ("Investigating", "investigating"),
            ("  resolved  ", "resolved"),
            ("\tClosed\n", "closed"),
            ("MONITORING", "monitoring"),
        ],
    )
    def test_case_and_whitespace_variants(self, value: str, expected: str) -> None:
        assert normalize_outage_status(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("in_progress", "investigating"),
            ("in-progress", "investigating"),
            ("in progress", "investigating"),
            ("acknowledged", "investigating"),
            ("ack", "investigating"),
            ("fixing", "identified"),
            ("root_cause", "identified"),
            ("watching", "monitoring"),
            ("fixed", "resolved"),
            ("done", "resolved"),
            ("complete", "resolved"),
            ("completed", "resolved"),
            ("ended", "closed"),
        ],
    )
    def test_alias_mappings(self, value: str, expected: str) -> None:
        assert normalize_outage_status(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
    def test_empty_inputs_return_none(self, value: object) -> None:
        assert normalize_outage_status(value) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", ["unknown", "bogus", "12345", "open!"])
    def test_unknown_values_return_none(self, value: str) -> None:
        assert normalize_outage_status(value) is None

    def test_non_string_input_returns_none(self) -> None:
        assert normalize_outage_status(123) is None  # type: ignore[arg-type]
        assert normalize_outage_status(object()) is None  # type: ignore[arg-type]


class TestNormalizePaymentStatus:
    """Tests for normalize_payment_status."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("pending", "pending"),
            ("processing", "processing"),
            ("succeeded", "succeeded"),
            ("failed", "failed"),
            ("refunded", "refunded"),
            ("cancelled", "cancelled"),
        ],
    )
    def test_canonical_values(self, value: str, expected: str) -> None:
        assert normalize_payment_status(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("PENDING", "pending"),
            ("Succeeded", "succeeded"),
            ("  failed  ", "failed"),
            ("\tRefunded\n", "refunded"),
        ],
    )
    def test_case_and_whitespace_variants(self, value: str, expected: str) -> None:
        assert normalize_payment_status(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("success", "succeeded"),
            ("successful", "succeeded"),
            ("ok", "succeeded"),
            ("paid", "succeeded"),
            ("complete", "succeeded"),
            ("completed", "succeeded"),
            ("error", "failed"),
            ("declined", "failed"),
            ("rejected", "failed"),
            ("in_progress", "processing"),
            ("in-progress", "processing"),
            ("in progress", "processing"),
            ("canceled", "cancelled"),
            ("void", "cancelled"),
            ("voided", "cancelled"),
            ("refund", "refunded"),
            ("awaiting", "pending"),
            ("queued", "pending"),
        ],
    )
    def test_alias_mappings(self, value: str, expected: str) -> None:
        assert normalize_payment_status(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
    def test_empty_inputs_return_none(self, value: object) -> None:
        assert normalize_payment_status(value) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", ["unknown", "bogus", "42", "pending!"])
    def test_unknown_values_return_none(self, value: str) -> None:
        assert normalize_payment_status(value) is None

    def test_non_string_input_returns_none(self) -> None:
        assert normalize_payment_status(3.14) is None  # type: ignore[arg-type]
        assert normalize_payment_status(None) is None


class TestNormalizeWebhookStatus:
    """Tests for normalize_webhook_status."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("pending", "pending"),
            ("delivering", "delivering"),
            ("delivered", "delivered"),
            ("failed", "failed"),
            ("retrying", "retrying"),
            ("dead", "dead"),
        ],
    )
    def test_canonical_values(self, value: str, expected: str) -> None:
        assert normalize_webhook_status(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("PENDING", "pending"),
            ("Delivered", "delivered"),
            ("  failed  ", "failed"),
            ("\tRetrying\n", "retrying"),
        ],
    )
    def test_case_and_whitespace_variants(self, value: str, expected: str) -> None:
        assert normalize_webhook_status(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("success", "delivered"),
            ("successful", "delivered"),
            ("ok", "delivered"),
            ("sent", "delivered"),
            ("complete", "delivered"),
            ("completed", "delivered"),
            ("error", "failed"),
            ("errored", "failed"),
            ("in_progress", "delivering"),
            ("in-progress", "delivering"),
            ("in progress", "delivering"),
            ("sending", "delivering"),
            ("retry", "retrying"),
            ("retries", "retrying"),
            ("exhausted", "dead"),
            ("abandoned", "dead"),
            ("gave_up", "dead"),
            ("gave-up", "dead"),
        ],
    )
    def test_alias_mappings(self, value: str, expected: str) -> None:
        assert normalize_webhook_status(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
    def test_empty_inputs_return_none(self, value: object) -> None:
        assert normalize_webhook_status(value) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", ["unknown", "bogus", "7", "delivered!"])
    def test_unknown_values_return_none(self, value: str) -> None:
        assert normalize_webhook_status(value) is None

    def test_non_string_input_returns_none(self) -> None:
        assert normalize_webhook_status([]) is None  # type: ignore[arg-type]
        assert normalize_webhook_status(None) is None


class TestNormalizeGenericStatus:
    """Tests for normalize_generic_status."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("active", "active"),
            ("inactive", "inactive"),
            ("open", "open"),
            ("closed", "closed"),
            ("pending", "pending"),
            ("resolved", "resolved"),
        ],
    )
    def test_canonical_values(self, value: str, expected: str) -> None:
        assert normalize_generic_status(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("ACTIVE", "active"),
            ("Inactive", "inactive"),
            ("  open  ", "open"),
            ("\tClosed\n", "closed"),
        ],
    )
    def test_case_and_whitespace_variants(self, value: str, expected: str) -> None:
        assert normalize_generic_status(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("enabled", "active"),
            ("on", "active"),
            ("disabled", "inactive"),
            ("off", "inactive"),
            ("in_progress", "in_progress"),
            ("in-progress", "in_progress"),
            ("in progress", "in_progress"),
            ("done", "resolved"),
            ("complete", "resolved"),
            ("completed", "resolved"),
            ("cancelled", "cancelled"),
            ("canceled", "cancelled"),
        ],
    )
    def test_alias_mappings(self, value: str, expected: str) -> None:
        assert normalize_generic_status(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
    def test_empty_inputs_return_none(self, value: object) -> None:
        assert normalize_generic_status(value) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", ["unknown", "bogus", "xyz", "active!"])
    def test_unknown_values_return_none(self, value: str) -> None:
        assert normalize_generic_status(value) is None

    def test_non_string_input_returns_none(self) -> None:
        assert normalize_generic_status(0) is None  # type: ignore[arg-type]
        assert normalize_generic_status(None) is None

    def test_custom_default_returned_for_unknown(self) -> None:
        assert normalize_generic_status("unknown", default="active") == "active"

    def test_custom_default_returned_for_empty(self) -> None:
        assert normalize_generic_status("", default="pending") == "pending"
        assert normalize_generic_status(None, default="pending") == "pending"

    def test_custom_default_not_used_for_valid_input(self) -> None:
        assert normalize_generic_status("open", default="closed") == "open"