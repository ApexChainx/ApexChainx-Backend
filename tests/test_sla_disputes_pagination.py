"""`GET /sla/disputes` must be paginated and capped — Issue #580.

The endpoint answered with ``query.all()``: every dispute in the table
serialized into one response, with no page metadata and no upper bound on the
payload. A caller could not tell "end of list" from "this page happens to be
short", and a large disputes table turned the list call into a full-table
dump. The endpoint now pages with the shared list cap (MAX_PAGE_SIZE = 200,
the same constant the audit list enforces — values above it are rejected with
422 by the query-param validation itself) and returns the standard envelope,
mirroring `GET /webhooks` (#554) and its deliveries (#296).

Pinned here too: the ordering is deterministic (`flagged_at` DESC with the
row id as tiebreaker — without it rows sharing a timestamp could repeat or
vanish between pages, making `has_more` lie), and the total comes from the
same statement as the page rather than a second COUNT(*) per request.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.sla_dispute import require_engineer
from app.db.session import get_db
from app.main import app
from app.models.sla_dispute import DisputeStatus

client = TestClient(app)


def _dispute(status: DisputeStatus = DisputeStatus.PENDING):
    return SimpleNamespace(
        id=uuid4(),
        sla_result_id=1,
        baseline_sla_result_id=None,
        proposed_sla_result_id=None,
        flagged_by="engineer@example.com",
        dispute_reason="Threshold was miscomputed for this outage",
        flagged_at=datetime(2026, 9, 1, 12, 0, 0),
        status=status,
        resolved_by=None,
        resolution_notes=None,
        resolved_at=None,
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
    """Rows as `add_columns(func.count().over())` returns them.

    The endpoint reads the window count as `paged[0].total_count` — real
    SQLAlchemy Rows support both tuple indexing and attribute access, so the
    stand-ins must too. Plain tuples would crash the attribute lookup.
    """

    class _Row:
        __slots__ = ("_values", "total_count")

        def __init__(self, item, count: int):
            self._values = (item, count)
            self.total_count = count

        def __getitem__(self, index):
            return self._values[index]

    return [_Row(item, total) for item in items]


def _install(rows, total: int):
    db, query = _mock_db(rows, total)

    def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    return query


@pytest.fixture
def engineer_override():
    def _fake_engineer():
        return SimpleNamespace(email="engineer@example.com", id="user_eng", role="engineer")

    app.dependency_overrides[require_engineer] = _fake_engineer
    yield
    app.dependency_overrides.pop(require_engineer, None)
    app.dependency_overrides.pop(get_db, None)


class TestResponseShape:
    def test_items_are_still_returned(self, engineer_override):
        _install(_paged_rows([_dispute(), _dispute()], 2), 2)

        resp = client.get("/api/v1/sla/disputes")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 2
        assert body["returned"] == 2

    def test_envelope_metadata_is_present(self, engineer_override):
        _install(_paged_rows([_dispute()], 45), 45)

        body = client.get("/api/v1/sla/disputes").json()

        assert set(body) == {"items", "total", "page", "page_size", "returned", "has_more"}

    def test_defaults_to_first_page_of_twenty(self, engineer_override):
        _install(_paged_rows([], 0), 0)

        body = client.get("/api/v1/sla/disputes").json()

        assert body["page"] == 1
        assert body["page_size"] == 20
        assert body["has_more"] is False


class TestPaging:
    def test_full_page_reports_more(self, engineer_override):
        _install(_paged_rows([_dispute() for _ in range(20)], 45), 45)

        body = client.get("/api/v1/sla/disputes").json()

        assert body["returned"] == 20
        assert body["has_more"] is True

    def test_partial_page_is_the_end(self, engineer_override):
        _install(_paged_rows([_dispute() for _ in range(5)], 45), 45)

        body = client.get("/api/v1/sla/disputes?page=3&page_size=20").json()

        assert body["returned"] == 5
        assert body["has_more"] is False

    def test_offset_is_derived_from_page_and_size(self, engineer_override):
        query = _install([], 45)

        client.get("/api/v1/sla/disputes?page=3&page_size=10")

        query.offset.assert_called_once_with(20)


class TestPageSizeCap:
    def test_page_size_at_the_shared_cap_is_accepted(self, engineer_override):
        _install(_paged_rows([], 0), 0)

        resp = client.get("/api/v1/sla/disputes?page_size=200")

        assert resp.status_code == 200
        assert resp.json()["page_size"] == 200

    @pytest.mark.parametrize("page_size", [0, 201, 1000])
    def test_page_size_out_of_range_is_rejected_with_422(self, engineer_override, page_size):
        _install(_paged_rows([], 0), 0)

        resp = client.get(f"/api/v1/sla/disputes?page_size={page_size}")

        assert resp.status_code == 422


class TestOrderingAndCount:
    def test_ordering_is_deterministic_with_a_tiebreaker(self, engineer_override):
        query = _install(_paged_rows([_dispute()], 1), 1)

        client.get("/api/v1/sla/disputes")

        # ORDER BY (flagged_at desc, id) must be part of the paged statement.
        # SQLAlchemy compiles each UnaryExpression freshly, so compare the
        # generated SQL text rather than expression object identity (same
        # approach as test_webhook_list_pagination.py).
        order_by_args = query.order_by.call_args[0]
        assert len(order_by_args) == 2
        assert "flagged_at" in str(order_by_args[0]) and "DESC" in str(order_by_args[0])
        assert "id" in str(order_by_args[1])

    def test_total_comes_from_the_page_statement(self, engineer_override):
        query = _install(_paged_rows([_dispute(), _dispute()], 45), 45)

        body = client.get("/api/v1/sla/disputes").json()

        assert body["total"] == 45
        query.count.assert_not_called()

    def test_empty_page_falls_back_to_a_count(self, engineer_override):
        query = _install([], 45)

        body = client.get("/api/v1/sla/disputes?page=99").json()

        assert body["total"] == 45
        query.count.assert_called_once()

    def test_status_filter_is_applied(self, engineer_override):
        query = _install([], 0)

        client.get("/api/v1/sla/disputes?status=pending")

        query.filter.assert_called_once()
