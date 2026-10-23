"""Endpoint tests for the TxIndexer read API — Issue #710.

These drive the routes through ``TestClient`` so the documented contract is the
contract the server actually honours: auth is required, filters reach the
repository untouched, bad query values are rejected before any query runs, and
a missing hash is a 404 with the RFC 7807 envelope.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.security import require_engineer
from app.db.session import get_db
from app.main import app
from app.repositories.tx_indexer_repository import TxIndexerRepository

CREATED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
HASH = "9c1f4f2f0f5b4a1d8c3e2b7a6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c"


def _row() -> SimpleNamespace:
    return SimpleNamespace(
        id=f"idx_{HASH}",
        transaction_hash=HASH,
        type="payment",
        amount=100_000_000,
        asset_code="USDC",
        from_address="GAAAA",
        to_address="GBBBB",
        status="confirmed",
        created_at=CREATED_AT,
        confirmed_at=CREATED_AT,
    )


@pytest.fixture
def client():
    app.dependency_overrides[require_engineer] = lambda: SimpleNamespace(role="engineer", email="eng@example.com")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def repo_calls(monkeypatch):
    """Capture what the route hands to the repository."""
    calls: dict[str, dict] = {}

    def fake_list(self, **kwargs):
        calls["list"] = kwargs
        return [_row()]

    def fake_get(self, transaction_hash):
        calls["get"] = {"transaction_hash": transaction_hash}
        return _row() if transaction_hash == HASH else None

    monkeypatch.setattr(TxIndexerRepository, "list_indexed", fake_list)
    monkeypatch.setattr(TxIndexerRepository, "get_by_hash", fake_get)
    return calls


class TestListTransactions:
    def test_requires_authentication(self):
        app.dependency_overrides.clear()
        with TestClient(app) as unauthenticated:
            response = unauthenticated.get("/api/v1/indexer/transactions")
        assert response.status_code == 401

    def test_returns_indexed_rows(self, client, repo_calls):
        response = client.get("/api/v1/indexer/transactions")

        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["transaction_hash"] == HASH
        assert body["items"][0]["amount"] == 100_000_000
        assert body["items"][0]["status"] == "confirmed"
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    def test_forwards_every_filter_to_the_repository(self, client, repo_calls):
        response = client.get(
            "/api/v1/indexer/transactions",
            params={
                "transaction_hash": HASH,
                "account": "GAAAA",
                "asset_code": "USDC",
                "status": "confirmed",
                "type": "payment",
                "created_after": "2026-10-01T00:00:00Z",
                "created_before": "2026-10-31T00:00:00Z",
                "limit": 25,
                "offset": 50,
            },
        )

        assert response.status_code == 200
        forwarded = repo_calls["list"]
        assert forwarded["transaction_hash"] == HASH
        assert forwarded["account"] == "GAAAA"
        assert forwarded["asset_code"] == "USDC"
        assert forwarded["status"] == "confirmed"
        assert forwarded["transaction_type"] == "payment"
        # One extra row is fetched to decide ``has_more`` without a count query.
        assert forwarded["limit"] == 26
        assert forwarded["offset"] == 50
        assert forwarded["after"] is None
        assert forwarded["created_after"] is not None
        assert forwarded["created_before"] is not None

    def test_limit_above_the_documented_cap_is_rejected(self, client, repo_calls):
        response = client.get("/api/v1/indexer/transactions", params={"limit": 201})
        assert response.status_code == 422
        assert "list" not in repo_calls

    def test_reversed_date_range_is_rejected_before_querying(self, client, repo_calls):
        response = client.get(
            "/api/v1/indexer/transactions",
            params={"created_after": "2026-10-31T00:00:00Z", "created_before": "2026-10-01T00:00:00Z"},
        )

        assert response.status_code == 422
        assert "created_after cannot be after created_before" in response.json()["detail"]
        assert "list" not in repo_calls


class TestGetTransaction:
    def test_returns_the_stored_transaction(self, client, repo_calls):
        response = client.get(f"/api/v1/indexer/transactions/{HASH}")

        assert response.status_code == 200
        assert response.json()["transaction_hash"] == HASH
        assert repo_calls["get"] == {"transaction_hash": HASH}

    def test_unknown_hash_is_a_documented_404(self, client, repo_calls):
        response = client.get("/api/v1/indexer/transactions/" + "0" * 64)

        assert response.status_code == 404
        body = response.json()
        assert body["detail"] == "Indexed transaction not found"
        assert body["error_code"] == "not_found"
        assert body["status"] == 404
