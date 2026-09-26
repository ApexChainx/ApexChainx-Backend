"""Issue #270 — API key scope registry, expiry validation, and status derivation.

- Creating a key with an unknown scope or a past (or now) expiry returns 422.
- ``list_api_keys`` exposes an effective status (active / expired / revoked).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import LoginRequest
from app.models.enums import Role
from app.services.api_key_store import create_api_key
from app.services.auth_store import AuthStore

PASSWORD = "TestPass123!"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def admin_headers(client, db):
    email = f"apikey-admin-{uuid.uuid4().hex[:10]}@example.com"
    AuthStore.admin_create_user(
        email=email,
        password=PASSWORD,
        full_name="Admin User",
        role=Role.admin,
        actor_id="system",
        actor_email="system@apexchainx.io",
        db=db,
    )
    session = AuthStore.login(LoginRequest(email=email, password=PASSWORD), db=db)
    return {"Authorization": f"Bearer {session.access_token}"}


def _create_key(client, admin_headers, **overrides):
    payload = {"name": "test-key", "scopes": ["sla:read"]}
    payload.update(overrides)
    return client.post("/api/v1/api-keys", json=payload, headers=admin_headers)


class TestScopeValidation:
    def test_unknown_scope_rejected(self, client, admin_headers):
        response = _create_key(client, admin_headers, scopes=["webhooks.read"])
        assert response.status_code == 422

    def test_typo_scope_rejected(self, client, admin_headers):
        # "sla:read " with trailing space and "sla.read" are both not in the registry.
        response = _create_key(client, admin_headers, scopes=["sla:read ", "sla.read"])
        assert response.status_code == 422

    def test_mixed_known_and_unknown_scope_rejected(self, client, admin_headers):
        response = _create_key(client, admin_headers, scopes=["sla:read", "payments.red"])
        assert response.status_code == 422

    def test_known_scopes_accepted(self, client, admin_headers):
        response = _create_key(client, admin_headers, scopes=["sla:read", "webhooks:write", "payments:read"])
        assert response.status_code == 201
        data = response.json()
        assert data["raw_key"].startswith("ak_")


class TestExpiryValidation:
    def test_past_expiry_rejected(self, client, admin_headers):
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        response = _create_key(client, admin_headers, expires_at=past)
        assert response.status_code == 422

    def test_expiry_equal_now_rejected_boundary(self, client, admin_headers):
        # Boundary: a key expiring right now is already dead, so it must be rejected.
        now = datetime.now(UTC).isoformat()
        response = _create_key(client, admin_headers, expires_at=now)
        assert response.status_code == 422

    def test_future_expiry_accepted(self, client, admin_headers):
        future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        response = _create_key(client, admin_headers, expires_at=future)
        assert response.status_code == 201


class TestListStatusDerivation:
    def test_list_exposes_active_expired_and_revoked(self, client, admin_headers, db):
        # Active key via the API (validated path).
        created = _create_key(client, admin_headers, name="active-key", scopes=["sla:read"])
        assert created.status_code == 201
        active_id = created.json()["id"]

        # Expired key — seeded directly through the store because the API
        # correctly rejects past expiries at creation time.
        expired_orm, _ = create_api_key(
            db=db,
            name="expired-key",
            scopes=["sla:read"],
            created_by="tester@example.com",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )

        # Revoked key — create then revoke via the API.
        revoked = _create_key(client, admin_headers, name="revoked-key", scopes=["sla:read"])
        assert revoked.status_code == 201
        revoked_id = revoked.json()["id"]
        revoke_response = client.delete(f"/api/v1/api-keys/{revoked_id}", headers=admin_headers)
        assert revoke_response.status_code == 200

        listed = client.get("/api/v1/api-keys", headers=admin_headers)
        assert listed.status_code == 200
        by_id = {item["id"]: item for item in listed.json()["keys"]}

        assert by_id[active_id]["status"] == "active"
        assert by_id[expired_orm.id]["status"] == "expired"
        assert by_id[revoked_id]["status"] == "revoked"
