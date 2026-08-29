"""Issue #271 — impersonation verification observability.

Every failed impersonation-token verification must:
1. Fall through to normal bearer-token auth (no 500, no impersonation).
2. Emit exactly one ``impersonation_verification_failed`` log line carrying a
   machine-readable ``reason`` and the request correlation ID.
3. Increment the ``impersonation_verification_failures`` counter tagged by
   reason.

A *valid* impersonation token must produce none of the above.
"""

import logging
import time
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import IMPERSONATION_VERIFICATION_FAILURES
from app.db.session import SessionLocal
from app.main import app
from app.models.auth import LoginRequest, RegisterRequest
from app.services.auth_store import AuthStore
from app.services.metrics import metrics

SECRET = settings.SECRET_KEY or "apexchainx-dev-secret"
PASSWORD = "TestPass123!"
FAILURE_MESSAGE = "impersonation_verification_failed"


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


def _failure_records(caplog):
    return [r for r in caplog.records if r.getMessage() == FAILURE_MESSAGE]


def _counter_delta(before: dict, reason: str) -> float:
    key = f"{IMPERSONATION_VERIFICATION_FAILURES}{{reason={reason}}}"
    after = metrics.get_metrics_summary()["counters"]
    return after.get(key, 0.0) - before.get(key, 0.0)


def _counters_snapshot() -> dict:
    return dict(metrics.get_metrics_summary()["counters"])


def _mint_impersonation_token(sub: str, *, exp_offset: int = 900, scope: str = "impersonate", secret: str = SECRET) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": "user@example.com",
        "act": "admin-id",
        "iat": now,
        "exp": now + exp_offset,
        "scope": scope,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class TestImpersonationFailureObservability:
    @pytest.mark.parametrize(
        "token_factory,expected_reason",
        [
            # Malformed: not even a JWT.
            (lambda: "not-a-real-token", "malformed"),
            # Bad signature: structurally valid JWT signed with a different secret.
            (lambda: _mint_impersonation_token("user_00000000", secret="wrong-secret"), "bad_signature"),
            # Expired: valid JWT whose exp is in the past.
            (lambda: _mint_impersonation_token("user_00000000", exp_offset=-60), "expired"),
            # Wrong scope: valid JWT that is not an impersonation token.
            (lambda: _mint_impersonation_token("user_00000000", scope="outages:read"), "wrong_scope"),
        ],
    )
    def test_failed_verification_falls_through_and_records(
        self, client, caplog, token_factory, expected_reason
    ):
        caplog.set_level(logging.WARNING)
        before = _counters_snapshot()
        correlation_id = f"test-corr-{uuid.uuid4().hex[:12]}"

        with caplog.at_level(logging.WARNING):
            response = client.get(
                "/api/v1/auth/me",
                headers={
                    "Authorization": f"Bearer {token_factory()}",
                    "X-Correlation-ID": correlation_id,
                },
            )

        # Falls through to normal bearer auth: the garbage token is not a real
        # session, so /auth/me must 401 — never 500, never a successful login.
        assert response.status_code == 401

        records = _failure_records(caplog)
        assert len(records) == 1, f"expected exactly one failure record, got {len(records)}"
        assert records[0].reason == expected_reason
        assert records[0].correlation_id == correlation_id
        # Exactly one counter increment for this reason.
        assert _counter_delta(before, expected_reason) == 1.0

    def test_valid_impersonation_token_produces_no_failure_records(self, client, caplog, db):
        email = f"imp-obs-{uuid.uuid4().hex[:10]}@example.com"
        AuthStore.register(
            RegisterRequest(email=email, password=PASSWORD, full_name="Target User"),
            db=db,
        )
        login = AuthStore.login(LoginRequest(email=email, password=PASSWORD), db=db)
        token = _mint_impersonation_token(login.user.id, exp_offset=900)

        caplog.set_level(logging.WARNING)
        before = _counters_snapshot()
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}", "X-Correlation-ID": "test-corr-valid"},
        )
        assert response.status_code == 200
        assert response.json()["id"] == login.user.id

        assert _failure_records(caplog) == []
        # No failure counter may be newly created or incremented.
        after = _counters_snapshot()
        new_failures = {
            k: v for k, v in after.items() if k not in before and k.startswith(IMPERSONATION_VERIFICATION_FAILURES)
        }
        assert new_failures == {}
        changed = {k: v for k, v in after.items() if k in before and before[k] != v}
        assert changed == {}
