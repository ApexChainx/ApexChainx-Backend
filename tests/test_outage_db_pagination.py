"""Tests for outage repository database-level pagination — Issue #235.

Validates that the list() method uses COUNT(*) OVER() for efficient
pagination and supports include_total=False to skip count entirely.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import require_engineer
from app.main import app
from app.repositories.outage_repository import OutageRepository


@pytest.fixture(autouse=True)
def engineer_auth():
    app.dependency_overrides[require_engineer] = lambda: SimpleNamespace(
        email="engineer@example.com", id="user_eng", role="engineer"
    )
    yield
    app.dependency_overrides.pop(require_engineer, None)


def _query():
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.add_columns.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    return query


class TestOutageListIncludeTotal:
    """Test that include_total parameter is passed and respected."""

    @patch("app.repositories.outage_repository._orm_to_pydantic", side_effect=lambda value: value)
    def test_include_total_true(self, _orm_to_pydantic):
        db = MagicMock()
        query = _query()
        db.query.return_value = query
        query.all.return_value = [(object(), 7)]

        result = OutageRepository(db).list(include_total=True)

        assert "items" in result
        assert result["total"] == 7
        query.add_columns.assert_called_once()

    @patch("app.repositories.outage_repository._orm_to_pydantic", side_effect=lambda value: value)
    def test_include_total_false_skips_count(self, _orm_to_pydantic):
        db = MagicMock()
        query = _query()
        db.query.return_value = query
        query.all.return_value = []

        result = OutageRepository(db).list(include_total=False)

        assert result["items"] == []
        assert result["total"] is None
        query.add_columns.assert_not_called()


class TestOutageListWindowFunction:
    """Test that COUNT(*) OVER() is used for total when include_total=True."""

    @patch("app.repositories.outage_repository.func")
    @patch("app.repositories.outage_repository._orm_to_pydantic", side_effect=lambda value: value)
    def test_uses_count_over(self, _orm_to_pydantic, mock_func):
        db = MagicMock()
        query = _query()
        db.query.return_value = query
        query.all.return_value = [(object(), 42)]

        OutageRepository(db).list(include_total=True)

        mock_func.count.return_value.over.assert_called_once_with()


class TestOutageEndpointIncludeTotal:
    """Test the API endpoint respects include_total parameter."""

    def test_endpoint_accepts_include_total(self):
        client = TestClient(app)

        resp = client.get("/api/v1/outages/?include_total=false")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_endpoint_returns_total_by_default(self):
        client = TestClient(app)

        resp = client.get("/api/v1/outages/")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
