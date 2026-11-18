"""Tests for admin user impersonation endpoint."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import get_password_hash
from app.db.session import SessionLocal
from app.main import app
from app.models.auth import LoginRequest, RegisterRequest
from app.models.enums import Role
from app.models.orm.user import UserORM
from app.repositories.user_repository import UserRepository
from app.services.auth_store import AuthStore

# Needs the Postgres service; skips with instructions when it is down (see tests/conftest.py).
pytestmark = [pytest.mark.postgres]


def _create_admin(db, email: str, password: str) -> None:
    UserRepository(db).create(
        user_id=f"user_{uuid4().hex[:8]}",
        email=email,
        hashed_password=get_password_hash(password),
        full_name="Admin User",
        role=Role.admin,
    )
    db.commit()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_headers(client):
    """Register an admin user and return auth headers with a valid token."""
    db = SessionLocal()
    try:
        email = f"imp-admin-{uuid4().hex}@example.com"
        password = "Admin123!"
        _create_admin(db, email, password)
        session = AuthStore.login(LoginRequest(email=email, password=password), db=db)
        yield {
            "Authorization": f"Bearer {session.access_token}",
            "email": email,
            "user_id": session.user.id,
        }
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def regular_user_headers(client):
    """Register a regular engineer user and return auth headers."""
    db = SessionLocal()
    try:
        email = f"imp-regular-{uuid4().hex}@example.com"
        password = "Regular123!"
        AuthStore.register(
            RegisterRequest(
                email=email,
                password=password,
                full_name="Regular User",
            ),
            db=db,
        )
        session = AuthStore.login(LoginRequest(email=email, password=password), db=db)
        yield {
            "Authorization": f"Bearer {session.access_token}",
            "email": email,
            "user_id": session.user.id,
        }
    finally:
        db.rollback()
        db.close()


class TestImpersonation:
    def test_admin_can_impersonate_regular_user(self, client, admin_headers, regular_user_headers):
        payload = {
            "user_id": regular_user_headers["user_id"],
            "reason": "Troubleshooting user-reported SLA calculation issue",
        }
        response = client.post(
            "/api/v1/auth/impersonate",
            json=payload,
            headers={"Authorization": admin_headers["Authorization"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        # The impersonation token should have act=admin claim
        assert data.get("acting_as") == regular_user_headers["user_id"]

    def test_impersonation_token_can_access_me(self, client, admin_headers, regular_user_headers):
        payload = {
            "user_id": regular_user_headers["user_id"],
            "reason": "Debugging access issue",
        }
        response = client.post(
            "/api/v1/auth/impersonate",
            json=payload,
            headers={"Authorization": admin_headers["Authorization"]},
        )
        assert response.status_code == 200
        imp_token = response.json()["access_token"]

        # Use the impersonation token to call /auth/me
        me_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {imp_token}"})
        assert me_response.status_code == 200
        me_data = me_response.json()
        assert me_data["id"] == regular_user_headers["user_id"]

    def test_cannot_impersonate_another_admin(self, client, admin_headers):
        # Register a second admin
        db = SessionLocal()
        try:
            admin2_email = f"imp-admin2-{uuid4().hex}@example.com"
            _create_admin(db, admin2_email, "Admin123!")
            admin2 = db.query(UserORM).filter(UserORM.email == admin2_email).first()
            admin2_id = admin2.id if admin2 else "unknown"
        finally:
            db.rollback()
            db.close()

        payload = {
            "user_id": admin2_id,
            "reason": "Testing admin impersonation guard",
        }
        response = client.post(
            "/api/v1/auth/impersonate",
            json=payload,
            headers={"Authorization": admin_headers["Authorization"]},
        )
        # Cannot impersonate another admin
        assert response.status_code == 403

    def test_reason_is_required(self, client, admin_headers, regular_user_headers):
        payload = {
            "user_id": regular_user_headers["user_id"],
            "reason": "",
        }
        response = client.post(
            "/api/v1/auth/impersonate",
            json=payload,
            headers={"Authorization": admin_headers["Authorization"]},
        )
        assert response.status_code == 422  # validation error for empty reason

    def test_non_admin_cannot_impersonate(self, client, regular_user_headers):
        payload = {
            "user_id": "some-user-id",
            "reason": "Should not work",
        }
        response = client.post(
            "/api/v1/auth/impersonate",
            json=payload,
            headers={"Authorization": regular_user_headers["Authorization"]},
        )
        assert response.status_code == 403

    def test_impersonation_without_auth_returns_401(self, client):
        payload = {
            "user_id": "some-user-id",
            "reason": "Should not work",
        }
        response = client.post("/api/v1/auth/impersonate", json=payload)
        assert response.status_code == 401
