"""Tests for GDPR right-to-erasure and data export endpoints."""

import io
import json
import tarfile

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import RegisterRequest
from app.services.auth_store import AuthStore


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client):
    """Register a test user and return auth headers with a valid token."""
    db = SessionLocal()
    try:
        email = f"gdpr-test-{id(object())}@example.com"
        password = "GdprTest123!"
        AuthStore.register(
            RegisterRequest(
                email=email,
                password=password,
                full_name="GDPR Test User",
            ),
            db=db,
        )
        from app.models.auth import LoginRequest

        session = AuthStore.login(LoginRequest(email=email, password=password), db=db)
        yield {
            "Authorization": f"Bearer {session.access_token}",
            "email": email,
            "user_id": session.user.id,
        }
    finally:
        db.rollback()
        db.close()


class TestGDPRExport:
    def test_export_returns_200_and_tarball(self, client, auth_headers):
        response = client.post("/api/v1/auth/me/export", headers={"Authorization": auth_headers["Authorization"]})
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/gzip"
        assert "gdpr_export.tar.gz" in response.headers.get("content-disposition", "")
        assert len(response.content) > 0

    def test_export_tarball_contains_valid_user_data(self, client, auth_headers):
        response = client.post("/api/v1/auth/me/export", headers={"Authorization": auth_headers["Authorization"]})
        tarball_bytes = response.content

        # Verify tarball is valid
        tar_buffer = io.BytesIO(tarball_bytes)
        with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
            member = tar.getmember("user_data.json")
            content = tar.extractfile(member).read()
            export = json.loads(content)
            assert "user" in export
            assert export["user"]["email"] == auth_headers["email"]

            meta_member = tar.getmember("_metadata.json")
            meta = json.loads(tar.extractfile(meta_member).read())
            assert "total_audit_log_entries" in meta
            assert meta["truncated"] is False

    def test_export_without_auth_returns_401(self, client):
        response = client.post("/api/v1/auth/me/export")
        assert response.status_code == 401

    def test_export_completes_quickly(self, client, auth_headers):
        import time

        start = time.time()
        response = client.post("/api/v1/auth/me/export", headers={"Authorization": auth_headers["Authorization"]})
        elapsed = time.time() - start
        assert response.status_code == 200
        # Should complete far under the 30 s SLO
        assert elapsed < 5.0, f"Export took {elapsed:.2f}s, expected < 5s"


class TestGDPRErase:
    def test_erase_returns_202_with_job_id(self, client, auth_headers):
        response = client.post("/api/v1/auth/me/erase", headers={"Authorization": auth_headers["Authorization"]})
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "accepted"

    def test_erase_without_auth_returns_401(self, client):
        response = client.post("/api/v1/auth/me/erase")
        assert response.status_code == 401

    def test_erase_soft_deletes_account(self, client, auth_headers):
        # First erase the account
        response = client.post("/api/v1/auth/me/erase", headers={"Authorization": auth_headers["Authorization"]})
        assert response.status_code == 202

        # After erasure, the token should be invalidated because sessions are revoked
        response2 = client.get("/api/v1/auth/me", headers={"Authorization": auth_headers["Authorization"]})
        assert response2.status_code == 401
