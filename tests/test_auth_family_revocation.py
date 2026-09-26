"""A deleted or compromised token family must stop authorizing access tokens (#535).

``logout-all`` deletes the user's token families, and refresh-time reuse detection
compromises them. The refresh path already refused both. The access path did not:
``AuthStore.get_user_for_token`` only looked at the session row, so a token whose
session outlived its family — issued in the same tick as the delete, or committed
after it was read — kept authenticating requests while ``POST /auth/refresh``
rejected the same session. These tests pin the family gate in place and cover the
race order the issue describes: issue, delete the family, then present the token.
"""

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.auth import LoginRequest, RegisterRequest
from app.models.orm.session import SessionORM
from app.repositories.token_family_repository import TokenFamilyRepository
from app.services.auth_store import AuthStore

PASSWORD = "RevokedFamily123!"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _register_and_login(db, label: str):
    """Register a throwaway user and log them in; return (login, family_id, email)."""
    email = f"{label}-{id(object())}@example.com"
    AuthStore.register(
        RegisterRequest(email=email, password=PASSWORD, full_name="Family Test"),
        db=db,
    )
    login = AuthStore.login(LoginRequest(email=email, password=PASSWORD), db=db)
    session_row = db.query(SessionORM).filter(SessionORM.email == email).one()
    return login, session_row.family_id, email


@pytest.fixture
def logged_in(db):
    return _register_and_login(db, "family-test")


def _delete_family(db, family_id: str) -> None:
    family = TokenFamilyRepository(db).get_family(family_id)
    assert family is not None
    db.delete(family)
    db.commit()


class TestDeletedFamilyRejectsTokens:
    def test_token_is_rejected_after_its_family_is_deleted(self, db, logged_in) -> None:
        login, family_id, _email = logged_in
        assert AuthStore.get_user_for_token(login.access_token, db=db) is not None

        _delete_family(db, family_id)

        assert AuthStore.get_user_for_token(login.access_token, db=db) is None

    def test_rejected_token_cannot_be_reused_afterwards(self, db, logged_in) -> None:
        """The stale session row is cleaned up, not just refused once."""
        login, family_id, email = logged_in
        _delete_family(db, family_id)

        assert AuthStore.get_user_for_token(login.access_token, db=db) is None
        assert AuthStore.get_user_for_token(login.access_token, db=db) is None
        assert db.query(SessionORM).filter(SessionORM.email == email).count() == 0

    def test_live_token_still_works_while_its_family_exists(self, db, logged_in) -> None:
        login, _family_id, email = logged_in

        user = AuthStore.get_user_for_token(login.access_token, db=db)

        assert user is not None
        assert user.email == email

    def test_me_returns_401_once_the_family_is_gone(self, db, logged_in) -> None:
        """End-to-end: the session row is still there, only the family is not."""
        login, family_id, _email = logged_in
        headers = {"Authorization": f"Bearer {login.access_token}"}
        _delete_family(db, family_id)

        with TestClient(app) as client:
            response = client.get("/api/v1/auth/me", headers=headers)

        assert response.status_code == 401

    def test_me_succeeds_while_the_family_exists(self, db, logged_in) -> None:
        login, _family_id, email = logged_in
        headers = {"Authorization": f"Bearer {login.access_token}"}

        with TestClient(app) as client:
            response = client.get("/api/v1/auth/me", headers=headers)

        assert response.status_code == 200
        assert response.json()["email"] == email


class TestCompromisedFamilyRejectsTokens:
    def test_token_is_rejected_once_its_family_is_compromised(self, db, logged_in) -> None:
        login, family_id, _email = logged_in
        TokenFamilyRepository(db).compromise_family(family_id)

        assert AuthStore.get_user_for_token(login.access_token, db=db) is None

    def test_is_revoked_treats_missing_and_compromised_alike(self, db, logged_in) -> None:
        _login, family_id, _email = logged_in
        repo = TokenFamilyRepository(db)

        assert repo.is_revoked(family_id) is False
        TokenFamilyRepository(db).compromise_family(family_id)
        assert repo.is_revoked(family_id) is True
        _delete_family(db, family_id)
        assert repo.is_revoked(family_id) is True

    def test_session_without_a_family_is_unaffected(self, db) -> None:
        """Pre-family sessions are migrated on refresh, not locked out here."""
        login, _family_id, email = _register_and_login(db, "legacy-test")
        session_row = db.query(SessionORM).filter(SessionORM.email == email).one()
        session_row.family_id = None
        db.commit()

        user = AuthStore.get_user_for_token(login.access_token, db=db)

        assert user is not None
        assert user.email == email
