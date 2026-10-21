"""Tests for the block poller's health endpoint (issue #730).

The poller has one failure mode that matters: a backlog of payments that
never drains. These tests pin that the endpoint notices it, that it says so
without exposing anything about the payments themselves, and that a database
it cannot read is reported as "down" rather than as "healthy".
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.base_class import Base
from app.db.session import get_db
from app.models.orm.payment import PaymentTransactionORM
from app.services.block_poller_health import build_block_poller_report


@pytest.fixture
def db() -> Session:
    """A session against an in-memory SQLite holding the payment table."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine, tables=[PaymentTransactionORM.__table__])
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _payment(db: Session, payment_id: str, created_at: datetime) -> PaymentTransactionORM:
    payment = PaymentTransactionORM(
        id=payment_id,
        transaction_hash=f"{payment_id}".ljust(64, "0"),
        type="sla_penalty",
        amount=100,
        asset_code="USDC",
        from_address="GPOOL",
        to_address="GOPS",
        status="pending",
        created_at=created_at,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


class TestReport:
    def test_an_empty_backlog_is_healthy(self, db):
        report = build_block_poller_report(db)

        assert report["status"] == "ok"
        assert report["components"]["block_poller.backlog"]["pending_payments"] == 0

    def test_a_fresh_backlog_is_healthy(self, db):
        _payment(db, "pay-1", datetime.now(UTC) - timedelta(seconds=5))

        report = build_block_poller_report(db)

        assert report["status"] == "ok"
        assert report["components"]["block_poller.backlog"]["pending_payments"] == 1

    def test_a_backlog_that_is_not_draining_is_degraded(self, db):
        _payment(db, "pay-1", datetime.now(UTC) - timedelta(hours=1))

        report = build_block_poller_report(db)

        assert report["status"] == "warn"
        assert report["components"]["block_poller.backlog"]["status"] == "warn"

    def test_a_confirmed_payment_is_not_part_of_the_backlog(self, db):
        payment = _payment(db, "pay-1", datetime.now(UTC) - timedelta(hours=1))
        payment.status = "confirmed"
        payment.confirmed_at = datetime.now(UTC)
        db.commit()

        report = build_block_poller_report(db)

        assert report["status"] == "ok"
        assert report["components"]["block_poller.backlog"]["pending_payments"] == 0

    def test_a_database_that_cannot_be_read_is_down(self):
        class BrokenSession:
            def query(self, *args, **kwargs):
                raise SQLAlchemyError("the database is gone")

        report = build_block_poller_report(BrokenSession())

        assert report["status"] == "down"
        assert report["components"]["block_poller.database"]["status"] == "down"

    def test_the_report_exposes_no_payment_identifiers(self, db):
        _payment(db, "pay-1", datetime.now(UTC) - timedelta(seconds=5))

        report = build_block_poller_report(db)

        assert "pay-1" not in str(report)


class TestEndpoint:
    def test_the_endpoint_reports_the_health(self, db, client: TestClient):
        from app.main import app

        app.dependency_overrides[get_db] = lambda: db
        try:
            response = client.get("/api/v1/block-poller/health")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "block_poller.backlog" in body["components"]
        assert body["timestamp"]

    def test_the_endpoint_reports_a_degraded_backlog(self, db, client: TestClient):
        from app.main import app

        _payment(db, "pay-1", datetime.now(UTC) - timedelta(hours=1))
        app.dependency_overrides[get_db] = lambda: db
        try:
            response = client.get("/api/v1/block-poller/health")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200
        assert response.json()["status"] == "warn"

    def test_the_endpoint_reports_a_broken_database_as_unavailable(self, client: TestClient):
        from app.main import app

        class BrokenSession:
            def query(self, *args, **kwargs):
                raise SQLAlchemyError("the database is gone")

        app.dependency_overrides[get_db] = lambda: BrokenSession()
        try:
            response = client.get("/api/v1/block-poller/health")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 503
        assert response.json()["status"] == "down"
