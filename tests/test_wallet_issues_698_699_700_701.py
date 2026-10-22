"""Tests covering WalletAPI issues assigned to Dujunior.

Issue #698 — Health check endpoint for WalletAPI
Issue #699 — Distributed tracing for WalletAPI
Issue #700 — SQL injection hardening for WalletAPI
Issue #701 — Pagination support for WalletAPI
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import require_engineer
from app.db.session import get_db
from app.main import app
from app.models.orm.wallet import WalletORM
from app.models.wallet import WalletCreateRequest, WalletLinkRequest
from app.repositories.wallet_repository import WalletRepository, _validate_input
from app.services.wallet_registry import WalletRegistry

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="function")
def engine():
    """In-memory SQLite engine for isolated test runs.

    Uses StaticPool so every connection goes to the same in-memory SQLite
    database — required because SQLite's :memory: databases are per-connection
    and we need the table created in setup to be visible to the session.

    Only create the wallets table — the full Base.metadata includes
    OutageORM which uses PostgreSQL ARRAY (not supported by SQLite).
    """
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WalletORM.metadata.create_all(bind=eng, tables=[WalletORM.__table__])
    yield eng
    WalletORM.metadata.drop_all(bind=eng, tables=[WalletORM.__table__])


@pytest.fixture(scope="function")
def db(engine) -> Session:
    """Fresh session per test."""
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db: Session) -> TestClient:
    """HTTP test client with SQLite DB and auth stubbed out."""
    # app is wrapped by PayloadSizeMiddleware; app.app is the inner FastAPI instance
    # that owns dependency_overrides.
    inner = app.app
    inner.dependency_overrides[get_db] = lambda: db
    inner.dependency_overrides[require_engineer] = lambda: SimpleNamespace(
        email="engineer@example.com", id="user_engineer", role="engineer"
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    inner.dependency_overrides.pop(get_db, None)
    inner.dependency_overrides.pop(require_engineer, None)


# ── Issue #700 — SQL injection hardening ──────────────────────────────────────


class TestSQLInjectionHardening:
    """Issue #700: WalletAPI must reject SQL injection attempts."""

    def test_validate_input_accepts_normal_user_id(self) -> None:
        """Regular alphanumeric user IDs must pass validation."""
        _validate_input("user-123", "user_id")  # No exception

    def test_validate_input_accepts_email(self) -> None:
        """Email-style user IDs are allowed (printable ASCII)."""
        _validate_input("user@example.com", "user_id")  # No exception

    def test_validate_input_rejects_null_byte(self) -> None:
        """Null bytes are a common injection vector."""
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_input("user\x00id", "user_id")

    def test_validate_input_rejects_control_characters(self) -> None:
        """Control characters outside printable ASCII are rejected."""
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_input("user\x01id", "user_id")

    def test_validate_input_rejects_select_keyword(self) -> None:
        """Classic SQLi SELECT keyword must be blocked."""
        with pytest.raises(ValueError, match="disallowed SQL"):
            _validate_input("' OR SELECT 1--", "user_id")

    def test_validate_input_rejects_union_select(self) -> None:
        """UNION SELECT is a classic extraction attack."""
        with pytest.raises(ValueError, match="disallowed SQL"):
            _validate_input("x' UNION SELECT * FROM users--", "user_id")

    def test_validate_input_rejects_drop_table(self) -> None:
        """DROP TABLE is a destructive injection pattern."""
        with pytest.raises(ValueError, match="disallowed SQL"):
            _validate_input("'; DROP TABLE wallets;--", "user_id")

    def test_validate_input_rejects_comment_sequence(self) -> None:
        """SQL comment sequences (--) are a common probe marker."""
        with pytest.raises(ValueError, match="disallowed SQL"):
            _validate_input("user--comment", "user_id")

    def test_validate_input_rejects_semicolon(self) -> None:
        """Semicolons allow query stacking."""
        with pytest.raises(ValueError, match="disallowed SQL"):
            _validate_input("user; SELECT 1", "user_id")

    def test_repository_get_by_user_id_rejects_injection(self, db: Session) -> None:
        """WalletRepository.get_by_user_id raises ValueError on SQLi payload."""
        repo = WalletRepository(db)
        with pytest.raises(ValueError):
            repo.get_by_user_id("' OR '1'='1")

    def test_repository_create_rejects_injection_in_user_id(self, db: Session) -> None:
        """WalletRepository.create raises ValueError on SQLi user_id."""
        repo = WalletRepository(db)
        with pytest.raises(ValueError):
            repo.create(user_id="'; DROP TABLE wallets;--", public_key="GABC")

    def test_repository_create_rejects_injection_in_public_key(self, db: Session) -> None:
        """WalletRepository.create raises ValueError on SQLi public_key."""
        repo = WalletRepository(db)
        with pytest.raises(ValueError):
            repo.create(user_id="normal-user", public_key="G' OR '1'='1")

    def test_http_create_with_sqli_user_id_returns_error(self, client: TestClient) -> None:
        """POST /wallets/create with SQLi user_id is rejected at service layer."""
        response = client.post(
            "/api/v1/wallets/create",
            json={"user_id": "' OR SELECT * FROM wallets--"},
        )
        # The validation raises ValueError which becomes a 500 or 422 –
        # either way it must not be 201 (wallet created).
        assert response.status_code not in (200, 201)

    def test_http_get_wallet_with_sqli_user_id_returns_error(self, client: TestClient) -> None:
        """GET /wallets/{user_id} with SQLi user_id is rejected."""
        response = client.get("/api/v1/wallets/' OR '1'='1")
        assert response.status_code not in (200, 201)


# ── Issue #701 — Pagination ────────────────────────────────────────────────────


class TestWalletPagination:
    """Issue #701: WalletAPI must support paginated listing."""

    def _seed_wallets(self, db: Session, n: int) -> None:
        """Create n wallets directly through the repository."""
        repo = WalletRepository(db)
        for i in range(n):
            repo.create(user_id=f"paguser-{i}", public_key=f"G{'A' * 54}{i:01}")

    def test_list_wallets_empty_db(self, db: Session) -> None:
        """list_wallets on an empty table returns empty items and total=0."""
        result = WalletRegistry.list_wallets(db, page=1, page_size=20)
        assert result.items == []
        assert result.total == 0
        assert result.page == 1
        assert result.page_size == 20

    def test_list_wallets_returns_all_when_under_page_size(self, db: Session) -> None:
        """All wallets appear on page 1 when total < page_size."""
        self._seed_wallets(db, 5)
        result = WalletRegistry.list_wallets(db, page=1, page_size=20)
        assert len(result.items) == 5
        assert result.total == 5

    def test_list_wallets_pagination_slices_correctly(self, db: Session) -> None:
        """Page 2 with page_size=3 from 7 rows has 3 items (not 4)."""
        self._seed_wallets(db, 7)
        result = WalletRegistry.list_wallets(db, page=2, page_size=3)
        assert len(result.items) == 3
        assert result.total == 7
        assert result.page == 2

    def test_list_wallets_last_page_has_remainder(self, db: Session) -> None:
        """Last page returns only the remaining rows."""
        self._seed_wallets(db, 7)
        result = WalletRegistry.list_wallets(db, page=3, page_size=3)
        assert len(result.items) == 1  # 7 rows, page 3 of 3 has 1

    def test_list_wallets_include_total_false(self, db: Session) -> None:
        """include_total=False skips the COUNT(*) OVER() — total is None."""
        self._seed_wallets(db, 3)
        result = WalletRegistry.list_wallets(db, page=1, page_size=20, include_total=False)
        assert result.total is None
        assert len(result.items) == 3

    def test_repository_list_wallets_window_function(self, db: Session) -> None:
        """WalletRepository.list_wallets returns correct dict structure."""
        repo = WalletRepository(db)
        repo.create(user_id="u1", public_key="G" + "A" * 55)
        repo.create(user_id="u2", public_key="G" + "B" * 55)

        result = repo.list_wallets(page=1, page_size=10, include_total=True)
        assert result["total"] == 2
        assert len(result["items"]) == 2
        assert result["page"] == 1
        assert result["page_size"] == 10

    def test_http_list_wallets_returns_200(self, client: TestClient) -> None:
        """GET /api/v1/wallets/ returns 200 with pagination envelope."""
        resp = client.get("/api/v1/wallets/")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "page" in body
        assert "page_size" in body

    def test_http_list_wallets_with_page_params(self, client: TestClient, db: Session) -> None:
        """page and page_size query params are respected over HTTP."""
        self._seed_wallets(db, 5)
        resp = client.get("/api/v1/wallets/?page=1&page_size=3")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 3
        assert body["total"] == 5
        assert body["page"] == 1
        assert body["page_size"] == 3

    def test_http_list_wallets_include_total_false(self, client: TestClient, db: Session) -> None:
        """include_total=false results in null total in the response."""
        resp = client.get("/api/v1/wallets/?include_total=false")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("total") is None

    def test_http_list_wallets_invalid_page_param(self, client: TestClient) -> None:
        """page=0 is rejected with 422 Unprocessable Entity."""
        resp = client.get("/api/v1/wallets/?page=0")
        assert resp.status_code == 422

    def test_http_list_wallets_invalid_page_size_too_large(self, client: TestClient) -> None:
        """page_size>100 is rejected with 422 Unprocessable Entity."""
        resp = client.get("/api/v1/wallets/?page_size=999")
        assert resp.status_code == 422


# ── Issue #699 — Distributed tracing ──────────────────────────────────────────


class TestDistributedTracing:
    """Issue #699: WalletAPI methods must emit OpenTelemetry spans."""

    def test_create_wallet_is_decorated_with_traced(self) -> None:
        """create_wallet must have the @traced wrapper applied."""
        # The __wrapped__ attribute is set by functools.wraps, confirming the
        # decorator was applied.  We check the span name via the closure.
        import inspect

        fn = WalletRegistry.create_wallet
        # functools.wraps preserves __wrapped__
        assert hasattr(fn, "__wrapped__"), "create_wallet must be decorated with @traced"

    def test_link_wallet_is_decorated_with_traced(self) -> None:
        """link_wallet must have the @traced wrapper applied."""
        fn = WalletRegistry.link_wallet
        assert hasattr(fn, "__wrapped__"), "link_wallet must be decorated with @traced"

    def test_get_wallet_is_decorated_with_traced(self) -> None:
        """get_wallet must have the @traced wrapper applied."""
        fn = WalletRegistry.get_wallet
        assert hasattr(fn, "__wrapped__"), "get_wallet must be decorated with @traced"

    def test_get_balance_is_decorated_with_traced(self) -> None:
        """get_balance must have the @traced wrapper applied."""
        fn = WalletRegistry.get_balance
        assert hasattr(fn, "__wrapped__"), "get_balance must be decorated with @traced"

    def test_get_status_is_decorated_with_traced(self) -> None:
        """get_status must have the @traced wrapper applied."""
        fn = WalletRegistry.get_status
        assert hasattr(fn, "__wrapped__"), "get_status must be decorated with @traced"

    def test_get_trustline_is_decorated_with_traced(self) -> None:
        """get_trustline must have the @traced wrapper applied."""
        fn = WalletRegistry.get_trustline
        assert hasattr(fn, "__wrapped__"), "get_trustline must be decorated with @traced"

    def test_get_funding_state_is_decorated_with_traced(self) -> None:
        """get_funding_state must have the @traced wrapper applied."""
        fn = WalletRegistry.get_funding_state
        assert hasattr(fn, "__wrapped__"), "get_funding_state must be decorated with @traced"

    def test_list_wallets_is_decorated_with_traced(self) -> None:
        """list_wallets must have the @traced wrapper applied."""
        fn = WalletRegistry.list_wallets
        assert hasattr(fn, "__wrapped__"), "list_wallets must be decorated with @traced"

    def test_traced_decorator_calls_tracer_start_span(self, db: Session) -> None:
        """Calling a traced method invokes the OTel tracer start_as_current_span."""
        with patch("app.core.tracing.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_span.__enter__ = lambda s: s
            mock_span.__exit__ = MagicMock(return_value=False)
            mock_tracer.start_as_current_span.return_value = mock_span

            WalletRegistry.list_wallets(db, page=1, page_size=5)

            mock_tracer.start_as_current_span.assert_called_once()
            call_args = mock_tracer.start_as_current_span.call_args
            # First positional arg is the span name
            assert call_args[0][0] == "wallet.list"

    def test_traced_spans_record_exceptions(self, db: Session) -> None:
        """When a traced method raises, the span records the exception."""
        with patch("app.core.tracing.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_span.__enter__ = lambda s: s
            mock_span.__exit__ = MagicMock(return_value=False)
            mock_tracer.start_as_current_span.return_value = mock_span

            from app.repositories.wallet_repository import _validate_input

            with pytest.raises(ValueError):
                WalletRegistry.get_wallet(db, "'; DROP TABLE wallets;--")

            # span.record_exception should have been called
            mock_span.record_exception.assert_called_once()


# ── Issue #698 — Health check endpoint ────────────────────────────────────────


class TestWalletHealthEndpoint:
    """Issue #698: WalletAPI must expose a /health endpoint."""

    def test_health_endpoint_returns_200_when_db_ok(self, client: TestClient) -> None:
        """GET /api/v1/wallets/health returns 200 when the DB is reachable."""
        resp = client.get("/api/v1/wallets/health")
        assert resp.status_code == 200

    def test_health_endpoint_response_shape(self, client: TestClient) -> None:
        """Response must contain status, components, and timestamp fields."""
        resp = client.get("/api/v1/wallets/health")
        body = resp.json()
        assert "status" in body
        assert "components" in body
        assert "timestamp" in body

    def test_health_endpoint_status_ok_when_db_reachable(self, client: TestClient) -> None:
        """status must be 'ok' when the wallet DB probe succeeds."""
        resp = client.get("/api/v1/wallets/health")
        assert resp.json()["status"] == "ok"

    def test_health_endpoint_contains_wallet_database_component(self, client: TestClient) -> None:
        """components must include a 'wallet_database' key."""
        resp = client.get("/api/v1/wallets/health")
        components = resp.json()["components"]
        assert "wallet_database" in components

    def test_health_endpoint_db_component_has_latency(self, client: TestClient) -> None:
        """wallet_database component must include latency_ms on success."""
        resp = client.get("/api/v1/wallets/health")
        db_comp = resp.json()["components"]["wallet_database"]
        assert db_comp["status"] == "ok"
        assert "latency_ms" in db_comp["details"]

    def test_health_endpoint_returns_503_when_db_down(self) -> None:
        """GET /api/v1/wallets/health returns 503 when the DB is unreachable."""
        from sqlalchemy.exc import OperationalError

        mock_db = MagicMock()
        mock_db.execute.side_effect = OperationalError("conn", {}, Exception("down"))

        inner = app.app
        inner.dependency_overrides[get_db] = lambda: mock_db
        inner.dependency_overrides[require_engineer] = lambda: SimpleNamespace(
            email="e@example.com", id="uid", role="engineer"
        )
        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/api/v1/wallets/health")
            assert resp.status_code == 503
        finally:
            inner.dependency_overrides.pop(get_db, None)
            inner.dependency_overrides.pop(require_engineer, None)

    def test_health_endpoint_no_auth_required(self) -> None:
        """Health check endpoint must not require authentication."""
        from fastapi.testclient import TestClient as TC

        # Don't override require_engineer — health endpoint should not have it
        db_override = MagicMock()
        db_override.execute.return_value = MagicMock()

        inner = app.app
        inner.dependency_overrides[get_db] = lambda: db_override
        # Intentionally NOT overriding require_engineer
        try:
            with TC(app, raise_server_exceptions=False) as c:
                resp = c.get("/api/v1/wallets/health")
            # Should succeed regardless of auth — health checks should not gate on auth
            assert resp.status_code in (200, 503)
        finally:
            inner.dependency_overrides.pop(get_db, None)

    def test_health_timestamp_is_iso_format(self, client: TestClient) -> None:
        """Timestamp in the health response must be a valid ISO-8601 string."""
        from datetime import datetime

        resp = client.get("/api/v1/wallets/health")
        ts = resp.json()["timestamp"]
        # Should parse without raising
        datetime.fromisoformat(ts)
