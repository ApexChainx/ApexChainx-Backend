"""Regression tests for issue #262.

Public registration must never create admin accounts.  Admin users must be
created through the dedicated POST /auth/admin/users endpoint.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.models.enums import Role

client = TestClient(app)


class TestPublicRegistrationRoleEnforcement:
    """Verify that the public register endpoint always creates engineer accounts."""

    def test_register_without_role_creates_engineer(self):
        """A normal registration (no role field) yields an engineer user."""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "email": "normal_register@example.com",
                "password": "Passw0rd!",
                "full_name": "Normal User",
            },
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["role"] == Role.engineer.value

    def test_register_with_role_admin_rejected(self):
        """Sending 'role': 'admin' in the body is rejected (unknown field → 422)."""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "email": "admin_register@example.com",
                "password": "Passw0rd!",
                "full_name": "Evil User",
                "role": "admin",
            },
        )
        # Pydantic v2 rejects unknown fields by default → 422
        assert resp.status_code == 422, resp.json()

    def test_register_with_role_engineer_rejected(self):
        """Sending any role value (even 'engineer') is rejected as unknown field."""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "email": "engineer_register@example.com",
                "password": "Passw0rd!",
                "full_name": "Crafty User",
                "role": "engineer",
            },
        )
        assert resp.status_code == 422, resp.json()

    def test_no_admin_user_created_from_public_register(self):
        """Even if the role field is somehow ignored, no admin user should exist."""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "email": "sneaky_admin@example.com",
                "password": "Passw0rd!",
                "full_name": "Sneaky Admin",
            },
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["role"] != Role.admin.value


class TestEngineerCannotAccessAdminRoutes:
    """Engineers must not be able to call admin-gated endpoints."""

    def _register_and_login(self, email: str = "eng_user@example.com") -> str:
        """Register a user and return the access token."""
        client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "Passw0rd!",
                "full_name": "Engineer User",
            },
        )
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Passw0rd!"},
        )
        assert login_resp.status_code == 200
        return login_resp.json()["access_token"]

    def test_engineer_cannot_create_webhook(self):
        """Engineer role is rejected by require_admin on webhook creation."""
        token = self._register_and_login("eng_webhook@example.com")
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "name": "test-hook",
                "url": "https://example.com/hook",
                "events": ["sla.violation"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.json()

    def test_engineer_cannot_access_admin_session_inventory(self):
        """Engineer role is rejected on admin session inventory."""
        token = self._register_and_login("eng_sessions@example.com")
        resp = client.get(
            "/api/v1/auth/admin/sessions/someone@example.com",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.json()

    def test_engineer_cannot_create_user_via_admin_endpoint(self):
        """Engineer role is rejected on admin user creation endpoint."""
        token = self._register_and_login("eng_create_user@example.com")
        resp = client.post(
            "/api/v1/auth/admin/users",
            json={
                "email": "newuser@example.com",
                "password": "Passw0rd!",
                "full_name": "New User",
                "role": "engineer",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.json()

    def test_unauthenticated_cannot_access_admin_routes(self):
        """No token → 401 on admin-gated routes."""
        resp = client.post(
            "/api/v1/webhooks",
            json={
                "name": "test-hook",
                "url": "https://example.com/hook",
                "events": ["sla.violation"],
            },
        )
        assert resp.status_code == 401


class TestAdminUserCreationEndpoint:
    """Verify the admin-only user creation endpoint works correctly."""

    def _get_admin_token(self) -> str:
        """Register an admin user via the admin endpoint.

        For this to work in tests, we need to seed an admin user.
        We'll use a mock approach — create a user directly and mock auth.
        """
        # We'll use the DB directly to create an admin user for testing
        from app.db.session import SessionLocal
        from app.core.security import get_password_hash
        from app.repositories.user_repository import UserRepository

        with SessionLocal() as db:
            repo = UserRepository(db)
            if not repo.get_by_email("test_admin@example.com"):
                from uuid import uuid4
                repo.create(
                    user_id=f"admin_{uuid4().hex[:8]}",
                    email="test_admin@example.com",
                    hashed_password=get_password_hash("Admin123!"),
                    full_name="Test Admin",
                    role=Role.admin,
                )

        # Now login to get a token
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": "test_admin@example.com", "password": "Admin123!"},
        )
        assert login_resp.status_code == 200
        return login_resp.json()["access_token"]

    def test_admin_can_create_engineer_user(self):
        """Admin can create a new engineer user via admin endpoint."""
        token = self._get_admin_token()
        resp = client.post(
            "/api/v1/auth/admin/users",
            json={
                "email": "created_engineer@example.com",
                "password": "Passw0rd!",
                "full_name": "Created Engineer",
                "role": "engineer",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["role"] == "engineer"
        assert data["email"] == "created_engineer@example.com"

    def test_admin_can_create_admin_user(self):
        """Admin can create another admin user via admin endpoint."""
        token = self._get_admin_token()
        resp = client.post(
            "/api/v1/auth/admin/users",
            json={
                "email": "created_admin@example.com",
                "password": "AdminPass1!",
                "full_name": "Created Admin",
                "role": "admin",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["role"] == "admin"

    def test_admin_create_user_duplicate_email_rejected(self):
        """Creating a user with a duplicate email returns 400."""
        token = self._get_admin_token()
        # First creation
        client.post(
            "/api/v1/auth/admin/users",
            json={
                "email": "dupe_user@example.com",
                "password": "Passw0rd!",
                "full_name": "User One",
                "role": "engineer",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        # Duplicate
        resp = client.post(
            "/api/v1/auth/admin/users",
            json={
                "email": "dupe_user@example.com",
                "password": "Passw0rd!",
                "full_name": "User Two",
                "role": "engineer",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_admin_create_user_weak_password_rejected(self):
        """Weak password is rejected on admin user creation."""
        token = self._get_admin_token()
        resp = client.post(
            "/api/v1/auth/admin/users",
            json={
                "email": "weak_pw@example.com",
                "password": "123",
                "full_name": "Weak PW",
                "role": "engineer",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "Password does not meet policy" in resp.json()["detail"]

    def test_unauthenticated_cannot_use_admin_create_user(self):
        """No token → 401 on admin user creation."""
        resp = client.post(
            "/api/v1/auth/admin/users",
            json={
                "email": "noauth@example.com",
                "password": "Passw0rd!",
                "full_name": "No Auth",
                "role": "engineer",
            },
        )
        assert resp.status_code == 401
