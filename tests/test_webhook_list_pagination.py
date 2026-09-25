"""`GET /webhooks` must return pagination metadata — Issue #554.

The endpoint applied `page`/`page_size` and then answered with a bare array.
A consumer paging through webhooks had no way to know it had reached the end,
so the only reliable move was to request one more page and see it come back
empty. The response is now an envelope carrying the same items plus `total`,
`page`, `page_size`, `returned` and `has_more`, mirroring
`PaginatedWebhookDeliveries`.

Two related defects are pinned here as well, because `has_more` is only true
pagination if they hold: the query had no `ORDER BY` (Postgres returns rows in
arbitrary order, so a webhook could appear on two pages or on none), and the
total is taken from the same statement as the page rather than a second COUNT.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.webhooks import require_admin
from app.db.session import get_db
from app.main import app
from app.models.webhook import Webhook

client = TestClient(app)


def _webhook(name: str = "outage-webhook", is_active: bool = True):
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        url="https://example.com/webhook",
        is_active=is_active,
        events=json.dumps(["sla.violation"]),
        max_retries=3,
        secret_version=1,
        last_secret_rotation_at=None,
    )


def _mock_db(rows, total: int):
    """A session whose query chain is self-returning, as SQLAlchemy's is."""
    query = MagicMock()
    query.filter.return_value = query
    query.add_columns.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = rows
    query.count.return_value = total

    db = MagicMock()
    db.query.return_value = query
    return db, query


def _paged_rows(items, total: int):
    """Rows as `add_columns(func.count().over())` returns them."""
    return [(item, total) for item in items]


def _override(mock_db):
    def _get_db_override():
        yield mock_db

    app.dependency_overrides[get_db] = _get_db_override


def _install(rows, total: int):
    """Install a mocked session and hand back its query mock for assertions."""
    db, query = _mock_db(rows, total)
    _override(db)
    return query


@pytest.fixture
def admin_override():
    def _fake_admin():
        return SimpleNamespace(email="webhook-admin@example.com", id="user_admin", role="admin")

    app.dependency_overrides[require_admin] = _fake_admin
    yield
    app.dependency_overrides.pop(require_admin, None)
    app.dependency_overrides.pop(get_db, None)


class TestResponseShape:
    def test_items_are_still_returned(self, admin_override):
        _install(_paged_rows([_webhook("a"), _webhook("b")], 2), 2)

        resp = client.get("/api/v1/webhooks")

        assert resp.status_code == 200
        body = resp.json()
        assert [item["name"] for item in body["items"]] == ["a", "b"]
        assert body["total"] == 2
        assert body["returned"] == 2

    def test_metadata_fields_are_present(self, admin_override):
        _install(_paged_rows([_webhook()], 45), 45)

        body = client.get("/api/v1/webhooks").json()

        assert set(body) == {"items", "total", "page", "page_size", "returned", "has_more"}

    def test_secrets_are_never_included(self, admin_override):
        _install(_paged_rows([_webhook()], 1), 1)

        item = client.get("/api/v1/webhooks").json()["items"][0]

        assert "secret" not in item
        assert item["schema_version"]


class TestPaging:
    def test_full_first_page_reports_more(self, admin_override):
        _install(_paged_rows([_webhook(f"w{index}") for index in range(20)], 45), 45)

        body = client.get("/api/v1/webhooks?page=1&page_size=20").json()

        assert body["page"] == 1
        assert body["page_size"] == 20
        assert body["returned"] == 20
        assert body["has_more"] is True

    def test_partial_last_page_is_the_end(self, admin_override):
        _install(_paged_rows([_webhook(f"w{index}") for index in range(5)], 45), 45)

        body = client.get("/api/v1/webhooks?page=3&page_size=20").json()

        assert body["returned"] == 5
        assert body["has_more"] is False

    def test_exact_multiple_does_not_claim_another_page(self, admin_override):
        """20 items over 20-item pages: the second page is the last one."""
        _install(_paged_rows([_webhook()] * 20, 20), 20)

        body = client.get("/api/v1/webhooks?page=1&page_size=20").json()

        assert body["has_more"] is False

    def test_empty_list_is_not_an_error(self, admin_override):
        _install([], 0)

        body = client.get("/api/v1/webhooks").json()

        assert body == {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
            "returned": 0,
            "has_more": False,
        }

    def test_page_past_the_end_is_empty(self, admin_override):
        _install([], 45)

        body = client.get("/api/v1/webhooks?page=99").json()

        assert body["items"] == []
        assert body["has_more"] is False

    def test_offset_is_derived_from_page_and_size(self, admin_override):
        query = _install([], 45)

        client.get("/api/v1/webhooks?page=3&page_size=10")

        query.offset.assert_called_once_with(20)


class TestQuery:
    def test_results_are_ordered_deterministically(self, admin_override):
        """Without ORDER BY, pages can repeat or skip rows and has_more lies."""
        query = _install(_paged_rows([_webhook()], 1), 1)

        client.get("/api/v1/webhooks")

        assert query.order_by.call_args[0] == (Webhook.created_at.desc(), Webhook.id)

    def test_total_comes_from_the_page_statement(self, admin_override):
        """The #296 single-statement pattern: no second COUNT(*) per request."""
        query = _install(_paged_rows([_webhook(), _webhook()], 45), 45)

        body = client.get("/api/v1/webhooks").json()

        assert body["total"] == 45
        query.count.assert_not_called()

    def test_empty_page_falls_back_to_a_count(self, admin_override):
        """An empty page has no window value to read, so count explicitly."""
        query = _install([], 45)

        body = client.get("/api/v1/webhooks?page=99").json()

        assert body["total"] == 45
        query.count.assert_called_once()

    def test_is_active_filter_is_applied(self, admin_override):
        query = _install([], 0)

        client.get("/api/v1/webhooks?is_active=false")

        query.filter.assert_called_once()

    def test_name_filter_is_applied(self, admin_override):
        query = _install([], 0)

        client.get("/api/v1/webhooks?name=outage")

        query.filter.assert_called_once()

    def test_both_filters_combine(self, admin_override):
        query = _install([], 0)

        client.get("/api/v1/webhooks?is_active=true&name=outage")

        assert query.filter.call_count == 2
