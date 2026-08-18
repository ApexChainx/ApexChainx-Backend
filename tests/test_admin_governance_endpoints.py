"""Tests for admin governance API endpoints — Issue #239.

Validates HTTP response shapes and audit logging for governance operations.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


@pytest.fixture
def admin_headers():
    """Bypass auth for testing — return minimal headers that pass require_admin."""
    return {}


class TestProposeAdminEndpoint:
    @patch("app.api.v1.endpoints.admin.audit_log")
    @patch("app.api.v1.endpoints.admin.propose_admin")
    def test_returns_200_with_tx_hash(self, mock_propose, mock_audit, admin_headers):
        mock_propose.return_value = {
            "tx_hash": "abc123",
            "pending_admin": "GBNEW",
            "status": "proposed",
            "contract_address": "test",
            "network": "testnet",
        }
        resp = client.post(
            "/api/v1/admin/propose-admin",
            json={"new_admin_address": "GBNEW"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tx_hash"] == "abc123"
        assert data["pending_admin"] == "GBNEW"

    @patch("app.api.v1.endpoints.admin.audit_log")
    @patch("app.api.v1.endpoints.admin.propose_admin")
    def test_audit_logged(self, mock_propose, mock_audit, admin_headers):
        mock_propose.return_value = {
            "tx_hash": "abc123",
            "pending_admin": "GBNEW",
            "status": "proposed",
            "contract_address": "test",
            "network": "testnet",
        }
        client.post(
            "/api/v1/admin/propose-admin",
            json={"new_admin_address": "GBNEW"},
            headers=admin_headers,
        )
        mock_audit.log.assert_called_once()
        call_kwargs = mock_audit.log.call_args[1]
        assert call_kwargs["event_type"] == "governance_propose_admin"
        assert call_kwargs["details"]["new_admin"] == "GBNEW"


class TestAcceptAdminEndpoint:
    @patch("app.api.v1.endpoints.admin.audit_log")
    @patch("app.api.v1.endpoints.admin.accept_admin")
    def test_returns_200(self, mock_accept, mock_audit, admin_headers):
        mock_accept.return_value = {
            "tx_hash": "def456",
            "status": "accepted",
            "contract_address": "test",
            "network": "testnet",
        }
        resp = client.post("/api/v1/admin/accept-admin", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["tx_hash"] == "def456"


class TestCancelAdminProposalEndpoint:
    @patch("app.api.v1.endpoints.admin.audit_log")
    @patch("app.api.v1.endpoints.admin.cancel_admin_proposal")
    def test_returns_200(self, mock_cancel, mock_audit, admin_headers):
        mock_cancel.return_value = {
            "tx_hash": "ghi789",
            "status": "cancelled",
            "contract_address": "test",
            "network": "testnet",
        }
        resp = client.post("/api/v1/admin/cancel-admin-proposal", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


class TestRenounceAdminEndpoint:
    @patch("app.api.v1.endpoints.admin.audit_log")
    @patch("app.api.v1.endpoints.admin.renounce_admin")
    def test_returns_200(self, mock_renounce, mock_audit, admin_headers):
        mock_renounce.return_value = {
            "tx_hash": "jkl012",
            "status": "renounced",
            "contract_address": "test",
            "network": "testnet",
        }
        resp = client.post("/api/v1/admin/renounce-admin", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "renounced"


class TestProposeOperatorEndpoint:
    @patch("app.api.v1.endpoints.admin.audit_log")
    @patch("app.api.v1.endpoints.admin.propose_operator")
    def test_returns_200_with_tx_hash(self, mock_propose, mock_audit, admin_headers):
        mock_propose.return_value = {
            "tx_hash": "mno345",
            "pending_operator": "GBOP",
            "status": "proposed",
            "contract_address": "test",
            "network": "testnet",
        }
        resp = client.post(
            "/api/v1/admin/propose-operator",
            json={"new_operator_address": "GBOP"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["pending_operator"] == "GBOP"


class TestAcceptOperatorEndpoint:
    @patch("app.api.v1.endpoints.admin.audit_log")
    @patch("app.api.v1.endpoints.admin.accept_operator")
    def test_returns_200(self, mock_accept, mock_audit, admin_headers):
        mock_accept.return_value = {
            "tx_hash": "pqr678",
            "status": "accepted",
            "contract_address": "test",
            "network": "testnet",
        }
        resp = client.post("/api/v1/admin/accept-operator", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["tx_hash"] == "pqr678"


class TestGovernanceErrorHandling:
    @patch("app.api.v1.endpoints.admin.audit_log")
    @patch("app.api.v1.endpoints.admin.propose_admin")
    def test_returns_400_on_governance_error(self, mock_propose, mock_audit, admin_headers):
        from app.services.contracts.governance_client import GovernanceError

        mock_propose.side_effect = GovernanceError("something broke")
        resp = client.post(
            "/api/v1/admin/propose-admin",
            json={"new_admin_address": "GBNEW"},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "something broke" in resp.json()["detail"]
        mock_audit.log.assert_not_called()
