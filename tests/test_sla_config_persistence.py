"""Issue #272 — SLA policy versions and publish tokens persist in the DB.

Previously the version ledger lived only in module-level dicts, so a restart
replayed versions (two different configs sharing one version number) and each
gunicorn worker kept a private token. These tests pin the DB-backed behaviour:

- Versions never repeat across a simulated process restart.
- Tokens fetched after a restart match the ledger, and a stale token yields
  ConcurrencyError (→ 409).
- Exactly one publish succeeds per token generation across two sessions.
- The unique (severity, policy_version) constraint converts a duplicate first
  publish into a ConcurrencyError instead of a raw IntegrityError.
"""

import pytest

import app.services.sla.config as sla_config
from app.db.session import SessionLocal
from app.models.orm.sla_config_history import SLAConfigHistoryORM
from app.models.sla import SLAConfigUpdateRequest
from app.services.sla.config import ConcurrencyError, get_current_token, get_policy_version, publish_config_for_severity

# Severities used by these tests. Rows are wiped at the start of each test so
# the assertions are deterministic regardless of earlier test runs.
TEST_SEVERITIES = ["low", "medium", "high"]

_ORIGINAL_CONFIG = {sev: dict(values) for sev, values in sla_config.SLA_CONFIG.items()}


def _reset_service_state() -> None:
    """Simulate a fresh process: the in-memory caches forget everything."""
    sla_config.SLA_CONFIG = {sev: dict(values) for sev, values in _ORIGINAL_CONFIG.items()}
    sla_config._policy_versions = {sev: 1 for sev in sla_config.SLA_CONFIG}
    sla_config._publish_tokens = {sev: "" for sev in sla_config.SLA_CONFIG}


def _wipe_history(db, severity: str) -> None:
    db.query(SLAConfigHistoryORM).filter(SLAConfigHistoryORM.severity == severity).delete()
    db.commit()


def _payload(**overrides) -> SLAConfigUpdateRequest:
    values = {"threshold_minutes": 10, "penalty_per_minute": 200, "reward_base": 900}
    values.update(overrides)
    return SLAConfigUpdateRequest(**values)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        _reset_service_state()


class TestVersionsSurviveRestart:
    def test_publish_versions_are_distinct_after_restart(self, db):
        severity = "low"
        _wipe_history(db, severity)
        _reset_service_state()

        policy1, token1, history1 = publish_config_for_severity(
            severity, _payload(), expected_token=None, published_by="eng1@example.com", db=db
        )

        # Simulate a process restart: the in-memory cache forgets everything.
        _reset_service_state()
        assert get_policy_version(severity) == 1  # cache is cold again
        # But the DB-backed reads still see the persisted ledger.
        assert get_policy_version(severity, db=db) == history1.policy_version
        assert get_current_token(severity, db=db) == token1

        policy2, token2, history2 = publish_config_for_severity(
            severity, _payload(threshold_minutes=99), expected_token=token1, db=db
        )
        # Two different publishes must never share a version number.
        assert history2.policy_version == history1.policy_version + 1
        assert history2.policy_version != history1.policy_version
        assert policy2.policy_version == history2.policy_version
        assert token2 != token1

        # The stale token (issued before the restart, then reused) now yields 409.
        with pytest.raises(ConcurrencyError):
            publish_config_for_severity(severity, _payload(), expected_token=token1, db=db)


class TestOptimisticConcurrency:
    def test_only_one_publish_succeeds_per_token_generation(self, db):
        severity = "medium"
        _wipe_history(db, severity)
        _reset_service_state()

        db1 = SessionLocal()
        db2 = SessionLocal()
        try:
            token = get_current_token(severity, db=db1)
            publish_config_for_severity(severity, _payload(), expected_token=token, published_by="a@example.com", db=db1)

            # Second session reuses the same token → must conflict.
            with pytest.raises(ConcurrencyError):
                publish_config_for_severity(severity, _payload(), expected_token=token, published_by="b@example.com", db=db2)

            # Exactly one history row was produced for this token generation.
            rows = db.query(SLAConfigHistoryORM).filter(SLAConfigHistoryORM.severity == severity).all()
            assert len(rows) == 1
        finally:
            db1.close()
            db2.close()

    def test_first_publish_race_unique_constraint_yields_conflict(self, db):
        """The unique (severity, policy_version) index is the backstop for the
        first-publish race: two workers can both pass the FOR UPDATE read (no
        row exists yet, so no lock is taken) and both insert the same version.
        The loser must get a ConcurrencyError, not a raw IntegrityError.
        """
        severity = "high"
        _wipe_history(db, severity)
        _reset_service_state()

        # Session 2 reads the ledger first, under REPEATABLE READ, so its
        # snapshot predates session 1's publish — exactly the race window.
        db2 = SessionLocal()
        try:
            db2.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            latest = (
                db2.query(SLAConfigHistoryORM)
                .filter(SLAConfigHistoryORM.severity == severity)
                .with_for_update()
                .first()
            )
            assert latest is None  # no row exists, so no lock is held

            # Session 1 publishes the first row.
            publish_config_for_severity(severity, _payload(), expected_token=None, published_by="a@example.com", db=db)

            # Simulate session 2 running in a *separate process*: its in-memory
            # cache never observed session 1's publish.
            _reset_service_state()

            # Session 2 still believes the version is free (stale snapshot and
            # stale cache) and attempts the same version → unique constraint → ConcurrencyError.
            with pytest.raises(ConcurrencyError):
                publish_config_for_severity(severity, _payload(), expected_token=None, published_by="b@example.com", db=db2)

            rows = db.query(SLAConfigHistoryORM).filter(SLAConfigHistoryORM.severity == severity).all()
            assert len(rows) == 1
        finally:
            db2.close()


class TestLedgerIsQueryable:
    def test_history_rows_persist_every_publish(self, db):
        severity = "low"
        _wipe_history(db, severity)
        _reset_service_state()

        publish_config_for_severity(severity, _payload(), expected_token=None, published_by="eng@example.com", db=db)
        publish_config_for_severity(severity, _payload(threshold_minutes=45), expected_token=None, published_by="eng@example.com", db=db)

        rows = (
            db.query(SLAConfigHistoryORM)
            .filter(SLAConfigHistoryORM.severity == severity)
            .order_by(SLAConfigHistoryORM.policy_version.asc())
            .all()
        )
        versions = [row.policy_version for row in rows]
        assert versions == sorted(versions)
        assert len(set(versions)) == len(versions)  # versions never repeat
        assert all(row.publish_token for row in rows)
        assert all(row.content_hash for row in rows)
        assert all(row.published_by == "eng@example.com" for row in rows)
