"""Atomic outage import must be all-or-nothing (#577).

``create_or_get_existing`` used to commit every row to the database as soon
as it was created. In ``atomic`` mode, a failure partway through a batch was
compensated for after the fact by deleting whatever had already been
committed -- which meant, for a brief window, another request could read
outages from a batch that ultimately failed. These tests exercise the real
``POST /outages/import`` endpoint against a real database and assert that a
row failing in the middle of a batch leaves none of that batch's rows behind.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.security import require_engineer
from app.db.session import get_db
from app.main import app as _served_app
from app.models.orm.outage import OutageORM

# app.main wraps the FastAPI instance in PayloadSizeMiddleware as its very
# last step (`app = PayloadSizeMiddleware(app)`), which has no
# `dependency_overrides` of its own. The original FastAPI instance is still
# reachable as its `.app` attribute -- same object, so overrides set here are
# seen by the same router that handles requests through the wrapped app.
app = _served_app.app

IMPORT_PATH = "/api/v1/outages/import"


def _row(outage_id: str, *, severity: str = "high", site_name: str | None = None) -> dict:
    # site_name/description vary per row: the app's own duplicate-detection
    # (site_name + detected_at + description + site_id) would otherwise treat
    # rows with identical content as the same outage regardless of id.
    return {
        "id": outage_id,
        "site_name": site_name or f"Site for {outage_id}",
        "site_id": "site-A",
        "severity": severity,
        "status": "open",
        "detected_at": "2026-01-01T00:00:00Z",
        "description": f"Test outage for atomic import coverage ({outage_id}).",
        "affected_services": ["core-api"],
    }


@pytest.fixture
def client(db):
    """Route the app's own get_db dependency to this test's real DB session."""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_engineer] = lambda: SimpleNamespace(
        email="engineer@example.com", id="user_engineer", role="engineer"
    )
    db.query(OutageORM).filter(OutageORM.id.like("outage-atomic%")).delete(synchronize_session=False)
    db.commit()
    with TestClient(_served_app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_engineer, None)


class TestAtomicImportRollsBackCompletely:
    def test_mid_batch_failure_persists_nothing(self, client: TestClient, db) -> None:
        # Pre-existing outage with different content than what the batch will
        # submit under the same id -- this is what makes row 2 fail.
        db.add(
            OutageORM(
                id="outage-atomic-conflict",
                site_name="Original Site",
                site_id="site-orig",
                severity="low",
                status="open",
                detected_at=datetime(2025, 6, 1, tzinfo=UTC),
                description="Pre-existing outage, untouched by the batch.",
                affected_services=["billing"],
            )
        )
        db.commit()

        rows = [
            _row("outage-atomic-1"),
            _row("outage-atomic-conflict", severity="critical"),  # conflicts -> fails mid-batch
            _row("outage-atomic-3"),
        ]

        resp = client.post(
            IMPORT_PATH,
            params={"consistency": "atomic"},
            files={"file": ("outages.json", json.dumps(rows), "application/json")},
        )

        # A genuine mid-batch conflict is an unexpected failure in this path
        # (not a validation error), so the endpoint reports it as a 500 --
        # what matters here is that nothing from the batch survives it.
        assert resp.status_code == 500, resp.text

        assert db.query(OutageORM).filter(OutageORM.id == "outage-atomic-1").first() is None
        assert db.query(OutageORM).filter(OutageORM.id == "outage-atomic-3").first() is None

        # The pre-existing row is untouched -- the batch never got to persist
        # anything, it never got deleted either.
        untouched = db.query(OutageORM).filter(OutageORM.id == "outage-atomic-conflict").first()
        assert untouched is not None
        assert untouched.site_name == "Original Site"
        assert untouched.severity == "low"

    def test_all_valid_rows_are_all_persisted(self, client: TestClient, db) -> None:
        rows = [_row("outage-atomic-ok-1"), _row("outage-atomic-ok-2"), _row("outage-atomic-ok-3")]

        resp = client.post(
            IMPORT_PATH,
            params={"consistency": "atomic"},
            files={"file": ("outages.json", json.dumps(rows), "application/json")},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["persisted"] == 3
        for outage_id in ("outage-atomic-ok-1", "outage-atomic-ok-2", "outage-atomic-ok-3"):
            assert db.query(OutageORM).filter(OutageORM.id == outage_id).first() is not None
