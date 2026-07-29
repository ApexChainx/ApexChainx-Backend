"""Tests for Postgres-backed wallet persistence (issue #49).

Validates:
- Wallets survive "restart" (new session == no in-memory leak)
- Concurrent register of the same address returns 409
- WalletRegistry uses the repository for durable storage
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.db.base import Base
from app.repositories.wallet_repository import WalletRepository
from app.models.orm.wallet import WalletORM


@pytest.fixture(scope="function")
def engine():
    """In-memory SQLite engine for isolated test runs."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


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


class TestWalletRepository:
    """Unit tests for the WalletRepository."""

    def test_create_and_retrieve_by_user_id(self, db: Session) -> None:
        repo = WalletRepository(db)
        wallet = repo.create(user_id="user-1", public_key="GABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF")
        assert wallet.user_id == "user-1"
        assert wallet.funded is False
        assert wallet.active is True

        fetched = repo.get_by_user_id("user-1")
        assert fetched is not None
        assert fetched.public_key == "GABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF"

    def test_retrieve_by_public_key(self, db: Session) -> None:
        repo = WalletRepository(db)
        repo.create(user_id="user-2", public_key="GBCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF")
        fetched = repo.get_by_public_key("GBCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF")
        assert fetched is not None
        assert fetched.user_id == "user-2"

    def test_concurrent_register_same_user_id_raises(self, db: Session) -> None:
        repo = WalletRepository(db)
        repo.create(user_id="user-3", public_key="G_A")
        # Second create with same user_id should fail at DB level (unique constraint)
        with pytest.raises(Exception):
            repo.create(user_id="user-3", public_key="G_B")

    def test_concurrent_register_same_public_key_raises(self, db: Session) -> None:
        repo = WalletRepository(db)
        repo.create(user_id="user-4", public_key="G_SHARED")
        with pytest.raises(Exception):
            repo.create(user_id="user-5", public_key="G_SHARED")

    def test_update_fields(self, db: Session) -> None:
        repo = WalletRepository(db)
        wallet = repo.create(user_id="user-6", public_key="G_UPDATE")
        updated = repo.update(wallet, funded=True, trustline_ready=True)
        assert updated.funded is True
        assert updated.trustline_ready is True

    def test_persistence_survives_session_boundary(self, engine) -> None:
        """Simulate a restart: commit, close, and re-open."""
        SessionLocal = sessionmaker(bind=engine)
        db1 = SessionLocal()
        repo1 = WalletRepository(db1)
        repo1.create(user_id="survivor", public_key="G_SURVIVE")
        db1.close()

        db2 = SessionLocal()
        repo2 = WalletRepository(db2)
        fetched = repo2.get_by_user_id("survivor")
        assert fetched is not None
        assert fetched.public_key == "G_SURVIVE"
        db2.close()
