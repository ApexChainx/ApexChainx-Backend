import pytest

from app.core.security import require_admin
from app.main import app
from app.services.contracts.governance_client import (
    GovernanceNotImplementedError,
    accept_admin,
    propose_admin,
    renounce_admin,
)


@pytest.mark.parametrize(
    "fn,args",
    [
        (propose_admin, ("GADDRESS123",)),
        (accept_admin, ()),
        (renounce_admin, ()),
    ],
)
def test_governance_ops_raise_when_disabled(settings, fn, args):
    """With GOVERNANCE_ENABLED off (the default), every governance op
    must fail loudly rather than fabricate a success response."""
    settings.GOVERNANCE_ENABLED = False
    with pytest.raises(GovernanceNotImplementedError):
        fn(*args)


def test_simulated_response_is_explicitly_labeled(settings):
    """When explicitly enabled for local testing, responses must be
    clearly marked as simulated and never claim on-chain completion."""
    settings.GOVERNANCE_ENABLED = True
    settings.CONTRACT_EXECUTION_MODE = "local_adapter"
    result = propose_admin("GADDRESS123")
    assert result["simulated"] is True
    assert "status" in result


def test_admin_endpoint_returns_501_when_not_implemented(client, settings):
    settings.GOVERNANCE_ENABLED = False
    app.dependency_overrides[require_admin] = lambda: {}
    try:
        response = client.post("/api/v1/admin/propose-admin", json={"new_admin_address": "GADDRESS123"})
    finally:
        app.dependency_overrides.pop(require_admin, None)
    assert response.status_code == 501
    assert response.json()["error_code"] == "not_implemented"
