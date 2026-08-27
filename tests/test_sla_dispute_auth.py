"""Issue #268 — dispute actor identity and read authentication.

The dispute endpoints previously trusted client-supplied ``flagged_by`` /
``resolved_by`` / ``created_by`` fields for the audit trail and left the two
read endpoints (``GET /sla/{id}/dispute``, ``GET /sla/{id}/dispute/history``)
completely unauthenticated. These tests pin the fixed behaviour:

- Actor values always come from the authenticated session, even when the
  request body attempts to forge them.
- Dispute reads require an authenticated engineer or admin session.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import LoginRequest, RegisterRequest
from app.models.enums import Role
from app.models.orm.outage import OutageORM
from app.models.orm.sla import SLAResultORM
from app.models.sla_dispute import DisputeAuditLog, SLADispute
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


def _login(db, email: str) -> dict[str, str]:
    session = AuthStore.login(LoginRequest(email=email, password=PASSWORD), db=db)
    return {"Authorization": f"Bearer {session.access_token}", "email": email}


def _register_engineer(db, email: str) -> dict[str, str]:
    AuthStore.register(
        RegisterRequest(email=email, password=PASSWORD, full_name="Engineer User"),
        db=db,
    )
    return _login(db, email)


def _create_admin(db, email: str) -> dict[str, str]:
    AuthStore.admin_create_user(
        email=email,
        password=PASSWORD,
        full_name="Admin User",
        role=Role.admin,
        actor_id="system",
        actor_email="system@apexchainx.io",
        db=db,
    )
    return _login(db, email)


@pytest.fixture
def engineer_headers(client, db):
    return _register_engineer(db, f"dispute-eng-{uuid.uuid4().hex[:10]}@example.com")


@pytest.fixture
def admin_headers(client, db):
    return _create_admin(db, f"dispute-admin-{uuid.uuid4().hex[:10]}@example.com")


@pytest.fixture
def sla_result_id(db):
    outage = OutageORM(
        id=f"outage-{uuid.uuid4().hex[:12]}",
        site_name="Test Site",
        severity="high",
        status="open",
        description="Outage used to seed a dispute test",
        affected_services=["api"],
    )
    db.add(outage)
    db.commit()
    result = SLAResultORM(
        outage_id=outage.id,
        status="met",
        mttr_minutes=30,
        threshold_minutes=60,
        amount=100,
        payment_type="reward",
        rating="excellent",
        is_latest=True,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result.id


def _flag(client, sla_result_id, headers, reason="Threshold was miscomputed for this outage"):
    return client.post(
        f"/api/v1/sla/{sla_result_id}/dispute",
        json={"dispute_reason": reason},
        headers={"Authorization": headers["Authorization"]},
    )


class TestDisputeActorDerivation:
    def test_flag_dispute_uses_authenticated_actor(self, client, engineer_headers, sla_result_id, db):
        forged = "forged@attacker.example"
        response = client.post(
            f"/api/v1/sla/{sla_result_id}/dispute",
            json={"dispute_reason": "Threshold was miscomputed for this outage", "flagged_by": forged},
            headers={"Authorization": engineer_headers["Authorization"]},
        )
        assert response.status_code == 201

        dispute = db.query(SLADispute).filter(SLADispute.sla_result_id == sla_result_id).first()
        assert dispute is not None
        # The forged client-supplied identity must not win.
        assert dispute.flagged_by == engineer_headers["email"]
        assert dispute.flagged_by != forged

        audit = db.query(DisputeAuditLog).filter(DisputeAuditLog.dispute_id == dispute.id).first()
        assert audit is not None
        assert audit.action == "flagged"
        assert audit.actor == engineer_headers["email"]

    def test_resolve_dispute_uses_authenticated_admin(self, client, engineer_headers, admin_headers, sla_result_id, db):
        assert _flag(client, sla_result_id, engineer_headers).status_code == 201

        forged = "forged@attacker.example"
        response = client.put(
            f"/api/v1/sla/{sla_result_id}/dispute/resolve",
            json={
                "status": "resolved",
                "resolution_notes": "Verified the computation and applied the corrected SLA",
                "resolved_by": forged,
                "apply_proposed": False,
            },
            headers={"Authorization": admin_headers["Authorization"]},
        )
        assert response.status_code == 200

        dispute = db.query(SLADispute).filter(SLADispute.sla_result_id == sla_result_id).first()
        assert dispute is not None
        assert dispute.resolved_by == admin_headers["email"]
        assert dispute.resolved_by != forged

        audit = (
            db.query(DisputeAuditLog)
            .filter(DisputeAuditLog.dispute_id == dispute.id)
            .order_by(DisputeAuditLog.recorded_at.desc())
            .first()
        )
        assert audit is not None
        assert audit.action == "resolved"
        assert audit.actor == admin_headers["email"]

    def test_proposed_sla_audit_actor_is_authenticated_engineer(self, client, engineer_headers, sla_result_id, db):
        assert _flag(client, sla_result_id, engineer_headers).status_code == 201

        forged = "forged@attacker.example"
        response = client.post(
            f"/api/v1/sla/{sla_result_id}/dispute/proposed",
            json={
                "severity": "high",
                "mttr_minutes": 45,
                "policy_version": "1.0",
                "created_by": forged,
            },
            headers={"Authorization": engineer_headers["Authorization"]},
        )
        assert response.status_code == 200

        dispute = db.query(SLADispute).filter(SLADispute.sla_result_id == sla_result_id).first()
        assert dispute is not None
        audit = (
            db.query(DisputeAuditLog)
            .filter(DisputeAuditLog.dispute_id == dispute.id, DisputeAuditLog.action == "proposed_sla_created")
            .first()
        )
        assert audit is not None
        assert audit.actor == engineer_headers["email"]
        assert audit.actor != forged


class TestDisputeReadsRequireAuth:
    def test_get_dispute_unauthenticated_returns_401(self, client, sla_result_id):
        response = client.get(f"/api/v1/sla/{sla_result_id}/dispute")
        assert response.status_code == 401

    def test_get_dispute_history_unauthenticated_returns_401(self, client, sla_result_id):
        response = client.get(f"/api/v1/sla/{sla_result_id}/dispute/history")
        assert response.status_code == 401

    def test_get_dispute_allows_engineer(self, client, engineer_headers, sla_result_id):
        assert _flag(client, sla_result_id, engineer_headers).status_code == 201
        response = client.get(
            f"/api/v1/sla/{sla_result_id}/dispute",
            headers={"Authorization": engineer_headers["Authorization"]},
        )
        assert response.status_code == 200
        assert response.json()["flagged_by"] == engineer_headers["email"]

    def test_get_dispute_allows_admin(self, client, engineer_headers, admin_headers, sla_result_id):
        assert _flag(client, sla_result_id, engineer_headers).status_code == 201
        response = client.get(
            f"/api/v1/sla/{sla_result_id}/dispute",
            headers={"Authorization": admin_headers["Authorization"]},
        )
        assert response.status_code == 200

    def test_get_dispute_history_allows_engineer(self, client, engineer_headers, sla_result_id):
        assert _flag(client, sla_result_id, engineer_headers).status_code == 201
        response = client.get(
            f"/api/v1/sla/{sla_result_id}/dispute/history",
            headers={"Authorization": engineer_headers["Authorization"]},
        )
        assert response.status_code == 200
        history = response.json()
        assert len(history) == 1
        assert history[0]["actor"] == engineer_headers["email"]
