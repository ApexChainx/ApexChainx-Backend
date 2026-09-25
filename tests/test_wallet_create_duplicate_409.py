"""Duplicate wallet registration must be a 409, not a 500 (issue #531).

``POST /api/v1/wallets/create`` is retried by clients, so a second create for a
user that already has a wallet has to answer 409 with the existing address. These
tests cover both the ordinary duplicate and the case the in-code pre-check cannot
see: a concurrent request that commits between the read and the insert, where the
unique constraint is the only thing that catches the collision.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import ApexWalletAlreadyExistsError
from app.core.security import require_engineer
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.orm.wallet import WalletORM
from app.models.wallet import WalletCreateRequest
from app.repositories.wallet_repository import WalletRepository
from app.services.wallet_registry import WalletRegistry

CREATE_PATH = "/api/v1/wallets/create"


@pytest.fixture(scope="function")
def db() -> Session:
    """In-memory SQLite session, like the other wallet persistence tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db: Session) -> TestClient:
    """Client with the wallet table backed by SQLite and auth stubbed out."""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_engineer] = lambda: SimpleNamespace(
        email="engineer@example.com", id="user_engineer", role="engineer"
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_engineer, None)


class TestDuplicateRegistration:
    def test_service_reports_existing_wallet_on_second_create(self, db: Session) -> None:
        first = WalletRegistry.create_wallet(db, WalletCreateRequest(user_id="user-1"))
        existing = WalletRepository(db).get_by_user_id("user-1")

        with pytest.raises(ApexWalletAlreadyExistsError) as excinfo:
            WalletRegistry.create_wallet(db, WalletCreateRequest(user_id="user-1"))

        conflict = excinfo.value
        assert conflict.status_code == 409
        assert conflict.error_code == "wallet_already_exists"
        assert conflict.fields == {
            "wallet_id": str(existing.id),
            "user_id": "user-1",
            "public_key": first.public_key,
        }

    def test_second_create_does_not_add_a_row(self, db: Session) -> None:
        first = WalletRegistry.create_wallet(db, WalletCreateRequest(user_id="user-1"))

        with pytest.raises(ApexWalletAlreadyExistsError):
            WalletRegistry.create_wallet(db, WalletCreateRequest(user_id="user-1"))

        rows = db.query(WalletORM).filter(WalletORM.user_id == "user-1").all()
        assert len(rows) == 1
        assert rows[0].public_key == first.public_key

    def test_concurrent_create_reports_conflict_instead_of_raising(self, db: Session, monkeypatch) -> None:
        """The pre-check can miss a row committed by a concurrent request.

        Simulated by hiding the existing row from the first read: the insert then
        trips the unique constraint, which is what really happens in production.
        """
        winner = WalletRepository(db).create(user_id="user-race", public_key="G_WINNER")
        real_get = WalletRepository.get_by_user_id
        calls = {"n": 0}

        def flaky_get(self, user_id: str):
            calls["n"] += 1
            if calls["n"] == 1:  # the pre-check runs before the winner is visible
                return None
            return real_get(self, user_id)

        monkeypatch.setattr(WalletRepository, "get_by_user_id", flaky_get)

        with pytest.raises(ApexWalletAlreadyExistsError) as excinfo:
            WalletRegistry.create_wallet(db, WalletCreateRequest(user_id="user-race"))

        assert excinfo.value.fields["wallet_id"] == str(winner.id)
        assert excinfo.value.fields["public_key"] == winner.public_key
        # The failed insert was rolled back, so the session is still usable.
        rows = db.query(WalletORM).filter(WalletORM.user_id == "user-race").all()
        assert [row.id for row in rows] == [winner.id]

    def test_different_users_get_different_wallets(self, db: Session) -> None:
        first = WalletRegistry.create_wallet(db, WalletCreateRequest(user_id="user-a"))
        second = WalletRegistry.create_wallet(db, WalletCreateRequest(user_id="user-b"))

        assert first.public_key != second.public_key


class TestDuplicateRegistrationOverHttp:
    def test_create_then_create_returns_409_with_existing_address(self, client: TestClient) -> None:
        created = client.post(CREATE_PATH, json={"user_id": "user-http"})
        assert created.status_code == 201, created.text
        public_key = created.json()["public_key"]

        conflict = client.post(CREATE_PATH, json={"user_id": "user-http"})

        assert conflict.status_code == 409
        body = conflict.json()
        assert body["error_code"] == "wallet_already_exists"
        assert body["fields"]["public_key"] == public_key
        assert body["fields"]["user_id"] == "user-http"

    def test_conflict_body_is_problem_json(self, client: TestClient) -> None:
        client.post(CREATE_PATH, json={"user_id": "user-http-2"})

        conflict = client.post(CREATE_PATH, json={"user_id": "user-http-2"})

        assert conflict.headers["content-type"].startswith("application/problem+json")
        assert conflict.json()["status"] == 409
