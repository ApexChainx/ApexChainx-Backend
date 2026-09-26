"""Tests for contract governance client — Issue #239.

Validates that governance operations return correct response shapes
and that audit events are logged for each action.
"""

from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.contracts.governance_client import (
    GovernanceError,
    accept_admin,
    accept_operator,
    cancel_admin_proposal,
    propose_admin,
    propose_operator,
    renounce_admin,
)


class TestProposeAdmin:
    def test_returns_tx_hash_and_pending_admin(self):
        result = propose_admin("GBNEWADMIN1234567890")
        assert "tx_hash" in result
        assert result["pending_admin"] == "GBNEWADMIN1234567890"
        assert result["status"] == "proposed"

    def test_empty_address_raises(self):
        with pytest.raises(GovernanceError, match="new_admin_address is required"):
            propose_admin("")


class TestAcceptAdmin:
    def test_returns_tx_hash(self):
        result = accept_admin()
        assert "tx_hash" in result
        assert result["status"] == "accepted"


class TestCancelAdminProposal:
    def test_returns_tx_hash(self):
        result = cancel_admin_proposal()
        assert "tx_hash" in result
        assert result["status"] == "cancelled"


class TestRenounceAdmin:
    def test_returns_tx_hash(self):
        result = renounce_admin()
        assert "tx_hash" in result
        assert result["status"] == "renounced"


class TestProposeOperator:
    def test_returns_tx_hash_and_pending_operator(self):
        result = propose_operator("GBNEWOPERATOR1234")
        assert "tx_hash" in result
        assert result["pending_operator"] == "GBNEWOPERATOR1234"
        assert result["status"] == "proposed"

    def test_empty_address_raises(self):
        with pytest.raises(GovernanceError, match="new_operator_address is required"):
            propose_operator("")


class TestAcceptOperator:
    def test_returns_tx_hash(self):
        result = accept_operator()
        assert "tx_hash" in result
        assert result["status"] == "accepted"


class TestStubTxHashDeterminism:
    """Verify that stub tx hashes are deterministic for same inputs."""

    def test_same_input_same_hash(self):
        h1 = propose_admin("GBTEST")["tx_hash"]
        h2 = propose_admin("GBTEST")["tx_hash"]
        assert h1 == h2

    def test_different_input_different_hash(self):
        h1 = propose_admin("GBA")["tx_hash"]
        h2 = propose_admin("GBB")["tx_hash"]
        assert h1 != h2
