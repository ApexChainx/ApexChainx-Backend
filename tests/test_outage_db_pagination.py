"""Tests for outage repository database-level pagination — Issue #235.

Validates that the list() method uses COUNT(*) OVER() for efficient
pagination and supports include_total=False to skip count entirely.

The OutageORM model stays real (its columns are needed to build valid
SQLAlchemy expressions for order_by/filter); only the session is mocked.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.models.orm.outage import OutageORM
from app.repositories.outage_repository import OutageRepository


def _outage_row():
    """A row rich enough for OutageRepository._orm_to_pydantic."""
    row = MagicMock(spec=OutageORM)
    row.id = "o-1"
    row.site_name = "site"
    row.site_id = None
    row.severity = "high"
    row.status = "open"
    row.detected_at = datetime.now(UTC)
    row.resolved_at = None
    row.description = "d"
    row.affected_services = []
    row.affected_subscribers = None
    row.assigned_to = None
    row.created_by = None
    row.location = None
    row.sla_status = None
    row.mttr_minutes = None
    row.created_at = datetime.now(UTC)
    row.updated_at = datetime.now(UTC)
    row.outage_events = []
    return row


def _mock_session(rows=None):
    """A session whose query chain is self-returning, as SQLAlchemy's is."""
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.add_columns.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = rows or []

    db = MagicMock()
    db.query.return_value = query
    return db, query


class TestOutageListIncludeTotal:
    """Test that include_total parameter is passed and respected."""

    def test_include_total_true(self):
        rows = [(_outage_row(), 0)]
        db, _query = _mock_session(rows)
        repo = OutageRepository(db)

        result = repo.list(include_total=True)

        assert "items" in result
        assert result["total"] == 0

    def test_include_total_false_skips_count(self):
        db, query = _mock_session([])
        repo = OutageRepository(db)

        result = repo.list(include_total=False)

        assert result["items"] == []
        assert result["total"] is None
        query.add_columns.assert_not_called()


class TestOutageListWindowFunction:
    """Test that COUNT(*) OVER() is used for total when include_total=True."""

    @patch("app.repositories.outage_repository.func")
    def test_uses_count_over(self, mock_func):
        db, _query = _mock_session([])
        repo = OutageRepository(db)

        repo.list(include_total=True)

        # Verify func.count().over() was called (window function)
        mock_func.count.assert_called()


class TestOutageEndpointIncludeTotal:
    """Test the API endpoint respects include_total parameter."""

    def test_endpoint_accepts_include_total(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from fastapi.testclient import TestClient

        from app.core.security import require_engineer
        from app.db.session import get_db
        from app.main import app

        app.dependency_overrides[require_engineer] = lambda: SimpleNamespace(id="user_test", role="engineer")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        try:
            client = TestClient(app)

            # Without include_total (defaults to true)
            resp = client.get("/api/v1/outages/?include_total=false")
            assert resp.status_code == 200
            data = resp.json()
            assert "items" in data
        finally:
            app.dependency_overrides.pop(require_engineer, None)
            app.dependency_overrides.pop(get_db, None)

    def test_endpoint_returns_total_by_default(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from fastapi.testclient import TestClient

        from app.core.security import require_engineer
        from app.db.session import get_db
        from app.main import app

        app.dependency_overrides[require_engineer] = lambda: SimpleNamespace(id="user_test", role="engineer")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        try:
            client = TestClient(app)

            resp = client.get("/api/v1/outages/")
            assert resp.status_code == 200
            data = resp.json()
            assert "total" in data
        finally:
            app.dependency_overrides.pop(require_engineer, None)
            app.dependency_overrides.pop(get_db, None)
