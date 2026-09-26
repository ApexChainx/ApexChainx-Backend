"""`max_retries` must be bounded on webhook create/update — Issue #552.

Both schemas took a bare `int`, so a client could store `max_retries: 1000000`
(or `-5`). Neither value is honoured: `webhook_service.dispatch_delivery` also
requires `retry_index < len(_get_retry_delays())`, so the delivery dead-letters
after a handful of attempts no matter what the webhook claims, and a negative
value dead-letters on the first failure. The stored number was a promise the
dispatcher never kept. The bound makes the accepted range explicit and the 422
message actionable.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.endpoints.webhooks import WebhookCreate, WebhookUpdate, require_admin
from app.core.config import settings
from app.db.session import get_db
from app.main import app

client = TestClient(app)

VALID_BODY = {
    "name": "outage-webhook",
    "url": "https://example.com/webhook",
    "events": ["sla.violation"],
}


@pytest.fixture
def admin_override():
    def _fake_admin():
        return SimpleNamespace(email="webhook-admin@example.com", id="user_admin", role="admin")

    app.dependency_overrides[require_admin] = _fake_admin
    # A rejected body never reaches the handler, but FastAPI resolves `get_db`
    # while building the request, so keep it off the real database.
    app.dependency_overrides[get_db] = MagicMock()
    yield
    app.dependency_overrides.pop(require_admin, None)
    app.dependency_overrides.pop(get_db, None)


class TestCreateSchema:
    @pytest.mark.parametrize("value", [0, 1, 3, settings.MAX_WEBHOOK_MAX_RETRIES])
    def test_in_range_values_pass(self, value):
        assert WebhookCreate(**VALID_BODY, max_retries=value).max_retries == value

    def test_default_is_still_three(self):
        assert WebhookCreate(**VALID_BODY).max_retries == 3

    @pytest.mark.parametrize("value", [-1, -5, settings.MAX_WEBHOOK_MAX_RETRIES + 1, 10**9])
    def test_out_of_range_values_are_rejected(self, value):
        with pytest.raises(ValidationError) as exc_info:
            WebhookCreate(**VALID_BODY, max_retries=value)
        assert exc_info.value.errors()[0]["loc"] == ("max_retries",)

    def test_bound_is_configurable(self):
        assert settings.MAX_WEBHOOK_MAX_RETRIES == 10


class TestUpdateSchema:
    def test_omitted_field_is_none(self):
        assert WebhookUpdate().max_retries is None

    @pytest.mark.parametrize("value", [0, 3, settings.MAX_WEBHOOK_MAX_RETRIES])
    def test_in_range_values_pass(self, value):
        assert WebhookUpdate(max_retries=value).max_retries == value

    @pytest.mark.parametrize("value", [-1, settings.MAX_WEBHOOK_MAX_RETRIES + 1, 999_999])
    def test_out_of_range_values_are_rejected(self, value):
        with pytest.raises(ValidationError) as exc_info:
            WebhookUpdate(max_retries=value)
        assert exc_info.value.errors()[0]["loc"] == ("max_retries",)


class TestCreateReturns422:
    def test_absurd_max_retries_is_rejected(self, admin_override):
        resp = client.post("/api/v1/webhooks", json=VALID_BODY | {"max_retries": 1_000_000})

        assert resp.status_code == 422
        assert any(detail["loc"][-1] == "max_retries" for detail in resp.json()["detail"])

    def test_negative_max_retries_is_rejected(self, admin_override):
        resp = client.post("/api/v1/webhooks", json=VALID_BODY | {"max_retries": -1})

        assert resp.status_code == 422

    def test_the_upper_bound_itself_is_not_a_422(self, admin_override):
        """The boundary value must reach the handler, not be refused as invalid.

        `get_db` is mocked, so the request cannot succeed; the point is that it
        gets past validation. `raise_server_exceptions=False` keeps a failure
        inside the handler from surfacing as a raised exception.
        """
        safe_client = TestClient(app, raise_server_exceptions=False)
        with patch("app.api.v1.endpoints.webhooks.validate_webhook_url"):
            resp = safe_client.post("/api/v1/webhooks", json=VALID_BODY | {"max_retries": settings.MAX_WEBHOOK_MAX_RETRIES})

        assert resp.status_code != 422


class TestUpdateReturns422:
    def test_absurd_max_retries_is_rejected(self, admin_override):
        resp = client.patch(f"/api/v1/webhooks/{uuid4()}", json={"max_retries": 1_000_000})

        assert resp.status_code == 422
        assert any(detail["loc"][-1] == "max_retries" for detail in resp.json()["detail"])

    def test_negative_max_retries_is_rejected(self, admin_override):
        resp = client.patch(f"/api/v1/webhooks/{uuid4()}", json={"max_retries": -2})

        assert resp.status_code == 422


class TestStartupValidation:
    def test_negative_configured_max_retries_is_a_config_error(self):
        from app.core.config import Settings, validate_critical_settings

        bad = Settings(MAX_WEBHOOK_MAX_RETRIES=-1)

        with pytest.raises(ValueError, match="MAX_WEBHOOK_MAX_RETRIES must be >= 0"):
            validate_critical_settings(bad)
