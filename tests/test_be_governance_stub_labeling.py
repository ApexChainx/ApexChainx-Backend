"""Governance client gating and stub labelling (THREAT_MODEL.md F-1).

- With GOVERNANCE_ENABLED off (the default), every governance op raises
  ``GovernanceNotImplementedError`` instead of fabricating a success payload.
- When explicitly enabled in local_adapter mode, responses are labeled
  ``simulated: true`` and never claim on-chain completion.
- The admin endpoint surfaces the disabled flag as 501 ``not_implemented``.
"""

import pytest

from app.core.config import settings as real_settings
from app.main import app
from app.core.security import get_current_user
from app.services.contracts.governance_client import (
    GovernanceNotImplementedError,
    accept_admin,
    propose_admin,
    renounce_admin,
)


@pytest.fixture
def settings():
    """The real settings object; tests flip flags explicitly."""
    return real_settings


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


def test_simulated_response_is_explicitly_labeled(settings, monkeypatch):
    """When explicitly enabled for local testing, responses must be
    clearly marked as simulated and never claim on-chain completion."""
    monkeypatch.setattr(settings, "GOVERNANCE_ENABLED", True)
    monkeypatch.setattr(settings, "CONTRACT_EXECUTION_MODE", "local_adapter")
    result = propose_admin("GADDRESS123")
    assert result["simulated"] is True
    assert "status" in result


def test_admin_endpoint_returns_501_when_not_implemented(client, settings, monkeypatch):
    settings.GOVERNANCE_ENABLED = False
    # The endpoint requires an admin; bypass get_current_user so the test
    # exercises the 501 gating, not authentication.
    admin = type("U", (), {"email": "gov-admin@example.com", "role": "admin"})()
    app.dependency_overrides[get_current_user] = lambda: admin
    try:
        response = client.post("/api/v1/admin/propose-admin", json={"new_admin_address": "GADDRESS123"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 501
    # app.exception_handlers wraps HTTPException detail into a problem-detail
    # body with the original payload under "errors".
    body = response.json()
    assert body["errors"][0]["error"] == "not_implemented"
