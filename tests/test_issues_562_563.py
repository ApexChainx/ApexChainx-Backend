"""Tests for issues #562 and #563.

#562 — Add a unique index on (webhook url + events) to stop duplicate webhooks
-----------------------------------------------------------------------
The partial unique index on ``(url, events) WHERE deleted_at IS NULL`` means
that a duplicate ``POST /webhooks`` must return 409 instead of 201 (or 500).

Covered:
- DuplicateWebhookError is raised by the helper on IntegrityError
- create_webhook catches IntegrityError, rolls back and raises HTTP 409
- The 409 body uses application/problem+json
- Different events → two registrations succeed
- Migration file exists with correct revision chain
- Migration upgrade emits CREATE UNIQUE INDEX covering url, events, deleted_at

#563 — Include request id in rate-limit and CSRF-style error responses
-----------------------------------------------------------------------
Every 4xx response must echo the *same* correlation ID that the
``CorrelationMiddleware`` captured from the incoming ``X-Correlation-ID``
header (or generated for that request).

Covered:
- _resolve_correlation_id reads request.state first
- _resolve_correlation_id falls back to context-var when state missing
- _resolve_correlation_id handles None request
- 422 (validation error) echoes caller-supplied X-Correlation-ID
- 4xx body correlation_id matches X-Correlation-ID header
- _problem_response emits a WARNING log with correlation_id in extra
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.exception_handlers import _problem_response, _resolve_correlation_id
from app.api.v1.endpoints.webhooks import require_admin
from app.db.session import get_db
from app.main import app
from app.services.webhook_uniqueness import (
    DuplicateWebhookError,
    handle_create_integrity_error,
    is_duplicate_webhook_error,
)
from app.utils.correlation_ctx import correlation_id_var

# Inner FastAPI app — needed for dependency_overrides and include_router
_inner_app = app.app  # PayloadSizeMiddleware.app == FastAPI instance

ADMIN_EMAIL = "test-admin@example.com"
CREATE_URL = "/api/v1/webhooks"
VALID_PAYLOAD = {
    "name": "my-webhook",
    "url": "https://hooks.example.com/endpoint",
    "events": ["sla.violation"],
}


@pytest.fixture(autouse=True)
def reset_correlation_id_var():
    """Ensure context var is clean before and after each test."""
    token = correlation_id_var.set(None)
    yield
    correlation_id_var.reset(token)


@pytest.fixture
def admin_override():
    def _fake_admin():
        return SimpleNamespace(email=ADMIN_EMAIL, id="user_admin", role="admin")

    _inner_app.dependency_overrides[require_admin] = _fake_admin
    yield
    _inner_app.dependency_overrides.pop(require_admin, None)


# ===========================================================================
# Issue #562 — Duplicate webhook → 409
# ===========================================================================


class TestWebhookUniqueness:
    """Acceptance: migration applies cleanly; duplicate create returns 409."""

    # --- unit-level: service helper ---

    def test_duplicate_webhook_error_is_raised_by_helper(self):
        """handle_create_integrity_error converts an IntegrityError to
        DuplicateWebhookError so the endpoint can catch a typed exception."""
        # Use a real IntegrityError with a psycopg2-style orig
        orig = Exception("UNIQUE constraint failed: webhooks.url, webhooks.events")
        real_exc = IntegrityError(
            statement="INSERT INTO webhooks ...",
            params={},
            orig=orig,
        )
        with pytest.raises(DuplicateWebhookError):
            handle_create_integrity_error(real_exc)

    def test_psycopg_unique_violation_is_recognised(self):
        """The real PostgreSQL wording (constraint name + DETAIL) is detected."""
        orig = Exception(
            'duplicate key value violates unique constraint "uq_webhooks_url_events_live"\n'
            "DETAIL:  Key (url, events)=(https://hooks.example.com/e, [\"sla.violation\"])"
            " already exists."
        )
        real_exc = IntegrityError(statement="INSERT INTO webhooks ...", params={}, orig=orig)
        assert is_duplicate_webhook_error(real_exc) is True
        with pytest.raises(DuplicateWebhookError):
            handle_create_integrity_error(real_exc)

    def test_unrelated_integrity_error_is_not_duplicate(self):
        """A NOT NULL / FK failure must not be misreported as a duplicate.

        ``handle_create_integrity_error`` returns normally so ``create_webhook``
        can re-raise the original ``IntegrityError`` for the global handler
        instead of claiming the url+events pair already exists.
        """
        orig = Exception('null value in column "name" violates not-null constraint')
        real_exc = IntegrityError(statement="INSERT INTO webhooks ...", params={}, orig=orig)
        assert is_duplicate_webhook_error(real_exc) is False
        # Must not raise DuplicateWebhookError
        handle_create_integrity_error(real_exc)

    # --- HTTP-level: duplicate create → 409 ---

    def test_duplicate_create_returns_409(self, admin_override):
        """create_webhook returns 409 when commit raises IntegrityError."""
        orig = Exception("UNIQUE constraint failed: webhooks.url, webhooks.events")
        integrity_error = IntegrityError(
            statement="INSERT INTO webhooks ...",
            params={},
            orig=orig,
        )

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0
        mock_session.query.return_value.all.return_value = []
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=integrity_error)
        mock_session.rollback = MagicMock()
        mock_session.refresh = MagicMock()

        _inner_app.dependency_overrides[get_db] = lambda: mock_session
        try:
            with patch(
                "app.api.v1.endpoints.webhooks.validate_webhook_url",
                return_value=["1.2.3.4"],
            ), patch(
                "app.api.v1.endpoints.webhooks._enforce_webhook_registration_cap",
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post(CREATE_URL, json=VALID_PAYLOAD)
        finally:
            _inner_app.dependency_overrides.pop(get_db, None)

        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["status"] == 409
        assert "already exists" in body["detail"].lower()

    def test_duplicate_create_returns_problem_json_content_type(self, admin_override):
        """The 409 body must be application/problem+json."""
        orig = Exception("UNIQUE constraint failed: webhooks.url, webhooks.events")
        integrity_error = IntegrityError(
            statement="INSERT INTO webhooks ...",
            params={},
            orig=orig,
        )

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0
        mock_session.query.return_value.all.return_value = []
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=integrity_error)
        mock_session.rollback = MagicMock()

        _inner_app.dependency_overrides[get_db] = lambda: mock_session
        try:
            with patch(
                "app.api.v1.endpoints.webhooks.validate_webhook_url",
                return_value=["1.2.3.4"],
            ), patch(
                "app.api.v1.endpoints.webhooks._enforce_webhook_registration_cap",
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post(CREATE_URL, json=VALID_PAYLOAD)
        finally:
            _inner_app.dependency_overrides.pop(get_db, None)

        assert resp.headers["content-type"].startswith("application/problem+json")

    def test_session_rollback_called_on_integrity_error(self, admin_override):
        """create_webhook must roll back the session before raising 409."""
        orig = Exception("UNIQUE constraint failed")
        integrity_error = IntegrityError(
            statement="INSERT ...", params={}, orig=orig
        )

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0
        mock_session.query.return_value.all.return_value = []
        mock_session.add = MagicMock()
        mock_session.commit = MagicMock(side_effect=integrity_error)
        mock_session.rollback = MagicMock()

        _inner_app.dependency_overrides[get_db] = lambda: mock_session
        try:
            with patch(
                "app.api.v1.endpoints.webhooks.validate_webhook_url",
                return_value=["1.2.3.4"],
            ), patch(
                "app.api.v1.endpoints.webhooks._enforce_webhook_registration_cap",
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post(CREATE_URL, json=VALID_PAYLOAD)
        finally:
            _inner_app.dependency_overrides.pop(get_db, None)

        assert resp.status_code == 409
        mock_session.rollback.assert_called_once()

    def test_migration_file_exists_and_has_correct_revision(self):
        """Migration 0031_webhook_url_events_unique must exist and chain correctly."""
        import importlib.util
        import os

        migration_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0031_webhook_url_events_unique.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0031", migration_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module.revision == "0031_webhook_url_events_unique"
        assert module.down_revision == "0030_webhook_soft_delete"

    def test_migration_upgrade_creates_unique_index(self):
        """upgrade() must issue a CREATE UNIQUE INDEX statement."""
        import importlib.util
        import os

        migration_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0031_webhook_url_events_unique.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0031_v2", migration_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        executed_sql: list[str] = []

        with patch("alembic.op.execute", side_effect=lambda sql: executed_sql.append(str(sql))):
            module.upgrade()

        assert executed_sql, "upgrade() must call op.execute()"
        combined = "\n".join(executed_sql).upper()
        assert "CREATE UNIQUE INDEX" in combined
        assert "WEBHOOKS" in combined
        assert "URL" in combined
        assert "EVENTS" in combined
        assert "DELETED_AT IS NULL" in combined

    def test_migration_upgrade_dedupes_before_creating_index(self):
        """Pre-existing duplicates must be collapsed *before* the index build.

        ``CREATE UNIQUE INDEX`` fails when duplicate (url, events) rows are
        already present, so the upgrade has to soft-delete the extras first or
        it would not apply cleanly on an existing database.
        """
        import importlib.util
        import os

        migration_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0031_webhook_url_events_unique.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0031_v2b", migration_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        executed_sql: list[str] = []

        with patch("alembic.op.execute", side_effect=lambda sql: executed_sql.append(str(sql))):
            module.upgrade()

        combined = "\n".join(executed_sql).upper()
        assert "UPDATE WEBHOOKS" in combined, "duplicates must be collapsed"
        assert "DELETED_AT = CURRENT_TIMESTAMP" in combined
        assert combined.index("UPDATE WEBHOOKS") < combined.index("CREATE UNIQUE INDEX")

    def test_migration_downgrade_drops_index(self):
        """downgrade() must drop the unique index."""
        import importlib.util
        import os

        migration_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0031_webhook_url_events_unique.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0031_v3", migration_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        executed_sql: list[str] = []

        with patch("alembic.op.execute", side_effect=lambda sql: executed_sql.append(str(sql))):
            module.downgrade()

        combined = "\n".join(executed_sql).upper()
        assert "DROP INDEX" in combined


# ===========================================================================
# Issue #563 — Correlation ID in 4xx error responses
# ===========================================================================


class TestCorrelationIdInErrorResponses:
    """Acceptance: 4xx responses carry the request id; logs correlate."""

    # --- unit-level: _resolve_correlation_id ---

    def test_resolve_reads_from_request_state_first(self):
        """When request.state.correlation_id is set it must be returned."""
        mock_request = MagicMock(spec=Request)
        mock_request.state = SimpleNamespace(correlation_id="state-cid-123")

        result = _resolve_correlation_id(mock_request)
        assert result == "state-cid-123"

    def test_resolve_falls_back_to_context_var_when_state_missing(self):
        """When request.state has no correlation_id the context var is used."""
        correlation_id_var.set("ctx-var-cid-456")

        mock_request = MagicMock(spec=Request)
        mock_request.state = SimpleNamespace()  # no correlation_id attribute

        result = _resolve_correlation_id(mock_request)
        assert result == "ctx-var-cid-456"

    def test_resolve_generates_when_no_context(self):
        """When neither request nor context var have a CID a non-empty string is returned."""
        correlation_id_var.set(None)

        mock_request = MagicMock(spec=Request)
        mock_request.state = SimpleNamespace()

        result = _resolve_correlation_id(mock_request)
        assert result  # non-empty

    def test_resolve_handles_none_request(self):
        """None request should not raise; falls back to context var."""
        correlation_id_var.set("fallback-cid-789")
        result = _resolve_correlation_id(None)
        assert result == "fallback-cid-789"

    # --- HTTP-level: 4xx echoes X-Correlation-ID ---

    def test_422_validation_error_echoes_correlation_id(self):
        """A 422 response must echo the caller's X-Correlation-ID."""
        cid = "caller-cid-for-422-test"
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/api/v1/auth/login",
                json={"not_valid": "payload"},
                headers={"X-Correlation-ID": cid},
            )
        assert resp.status_code in (400, 422)
        assert resp.headers.get("X-Correlation-ID") == cid
        assert resp.json().get("correlation_id") == cid

    def test_4xx_content_type_is_problem_json(self):
        """4xx error responses must use application/problem+json."""
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/api/v1/auth/login",
                json={"not_valid": "payload"},
            )
        assert resp.status_code in (400, 422)
        assert resp.headers["content-type"].startswith("application/problem+json")

    def test_4xx_body_and_header_correlation_ids_match(self):
        """The correlation_id in the body must equal the X-Correlation-ID header."""
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/api/v1/auth/login",
                json={"not_valid": "payload"},
            )
        assert resp.status_code in (400, 422)
        header_cid = resp.headers.get("X-Correlation-ID", "")
        body_cid = resp.json().get("correlation_id", "")
        assert header_cid != ""
        assert header_cid == body_cid

    def test_problem_response_echoes_request_state_correlation_id(self):
        """_problem_response must include the correlation ID from request state
        in its response body (not generate a fresh one)."""
        cid = "expected-cid-from-state"
        mock_request = MagicMock(spec=Request)
        mock_request.state = SimpleNamespace(correlation_id=cid)
        mock_request.url.path = "/test/path"

        resp = _problem_response(
            status=429,
            title="Too Many Requests",
            detail="Rate limit exceeded.",
            request=mock_request,
        )

        import json
        body = json.loads(resp.body)
        assert body["correlation_id"] == cid
        # Header must also carry the same value
        headers = dict(resp.headers)
        assert headers.get("x-correlation-id") == cid

    def test_problem_response_preserves_exception_headers(self):
        """Extra headers (e.g. Retry-After on 429) survive alongside the CID."""
        mock_request = MagicMock(spec=Request)
        mock_request.state = SimpleNamespace(correlation_id="cid-with-headers")
        mock_request.url.path = "/test/path"

        resp = _problem_response(
            status=429,
            title="Too Many Requests",
            detail="Rate limit exceeded.",
            request=mock_request,
            headers={"Retry-After": "60"},
        )

        headers = {k.lower(): v for k, v in resp.headers.items()}
        assert headers["retry-after"] == "60"
        assert headers["x-correlation-id"] == "cid-with-headers"

    def test_problem_response_logs_warning_for_4xx(self, caplog):
        """_problem_response must emit a WARNING log for 4xx responses."""
        cid = "log-test-cid-0001"
        mock_request = MagicMock(spec=Request)
        mock_request.state = SimpleNamespace(correlation_id=cid)
        mock_request.url.path = "/test/path"

        # The CorrelationIdFilter stamps get_correlation_id() onto every record,
        # overwriting any extra= we pass. Set the context var to match so the
        # filter produces the right value (this is what CorrelationMiddleware
        # does in production).
        correlation_id_var.set(cid)

        with caplog.at_level(logging.WARNING, logger="app.api.exception_handlers"):
            _problem_response(
                status=429,
                title="Too Many Requests",
                detail="Rate limit exceeded.",
                request=mock_request,
            )

        # A WARNING record must have been emitted
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records, "Expected a WARNING log for 4xx response"

        # The CorrelationIdFilter stamps correlation_id from the context var,
        # which we set to cid above.
        matching = [
            r for r in warning_records
            if getattr(r, "correlation_id", None) == cid
        ]
        assert matching, (
            f"No WARNING log record had correlation_id={cid!r}. "
            f"Records: {[(r.levelno, vars(r).get('correlation_id')) for r in warning_records]}"
        )
