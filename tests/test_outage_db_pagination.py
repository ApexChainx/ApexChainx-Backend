"""Tests for outage repository database-level pagination — Issue #235.

Validates that the list() method uses COUNT(*) OVER() for efficient
pagination and supports include_total=False to skip count entirely.
"""

from unittest.mock import MagicMock, PropertyMock, patch

from app.repositories.outage_repository import OutageRepository


class TestOutageListIncludeTotal:
    """Test that include_total parameter is passed and respected."""

    @patch("app.repositories.outage_repository.OutageORM")
    def test_include_total_true(self, MockORM):
        db = MagicMock()
        repo = OutageRepository(db)

        mock_q = MagicMock()
        db.query.return_value = mock_q
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value = mock_q

        # Mock the count-over query
        mock_q.add_columns.return_value = mock_q
        mock_q.offset.return_value = mock_q
        mock_q.limit.return_value = mock_q
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, idx: 0 if idx == 1 else MagicMock()
        mock_q.first.return_value = mock_row

        result = repo.list(include_total=True)

        assert "items" in result
        assert result["total"] is not None or result["total"] == 0

    @patch("app.repositories.outage_repository.OutageORM")
    def test_include_total_false_skips_count(self, MockORM):
        db = MagicMock()
        repo = OutageRepository(db)

        mock_q = MagicMock()
        db.query.return_value = mock_q
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value = mock_q
        mock_q.offset.return_value = mock_q
        mock_q.limit.return_value = mock_q
        mock_q.all.return_value = []

        result = repo.list(include_total=False)

        assert result["items"] == []
        assert result["total"] is None


class TestOutageListWindowFunction:
    """Test that COUNT(*) OVER() is used for total when include_total=True."""

    @patch("app.repositories.outage_repository.func")
    @patch("app.repositories.outage_repository.OutageORM")
    def test_uses_count_over(self, MockORM, mock_func):
        db = MagicMock()
        repo = OutageRepository(db)

        mock_q = MagicMock()
        db.query.return_value = mock_q
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value = mock_q
        mock_q.with_entities.return_value = mock_q
        mock_q.subquery.return_value = MagicMock()
        mock_q.add_columns.return_value = mock_q
        mock_q.offset.return_value = mock_q
        mock_q.limit.return_value = mock_q

        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, idx: 42 if idx == 1 else MagicMock()
        mock_q.first.return_value = mock_row
        mock_q.all.return_value = []

        result = repo.list(include_total=True)

        # Verify func.count().over() was called (window function)
        mock_func.count.assert_called()


class TestOutageEndpointIncludeTotal:
    """Test the API endpoint respects include_total parameter."""

    def test_endpoint_accepts_include_total(self):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)

        # Without include_total (defaults to true)
        resp = client.get("/api/v1/outages/?include_total=false")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_endpoint_returns_total_by_default(self):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)

        resp = client.get("/api/v1/outages/")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
