"""The primary incident view must stay index-backed as the table grows (#579).

Migration 0032_outage_status_detected_at_index adds a composite btree on
outages(status, detected_at DESC) so the dominant operator query -- status
filter + detected_at window, ordered newest-first -- is served by one index.
Whether Postgres actually uses it is a cost-based decision: on a near-empty
table a Seq Scan is the objectively correct choice regardless of indexes,
which is why this test seeds a large, realistic fixture and asserts the real
repository query plan stays index-backed.

Mirrors the #578 pattern in tests/test_outage_search_performance.py: the SQL
under test is captured from OutageRepository (never hand-copied), then
EXPLAINed and inspected for Seq Scan nodes over `outages`.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from scripts.check_performance_regression import check_outage_incident_view_plan

FIXTURE_ROWS = 50_000
SEED_MARKER = "incidentguard"


def _seed_outages(db, n: int = FIXTURE_ROWS) -> None:
    # Same shape as the #578 fixture: 500 sites, status and severity spread
    # evenly, detected_at spread over the past `n` minutes so a 30-day window
    # on status='open' is a realistic fraction of the table.
    db.execute(
        text(
            f"""
            INSERT INTO outages (
                id, site_name, site_id, severity, status, detected_at,
                description, affected_services, created_at, updated_at
            )
            SELECT
                '{SEED_MARKER}-' || gs,
                'Site ' || (gs % 500),
                'site-' || (gs % 500),
                (ARRAY['critical','high','medium','low'])[1 + (gs % 4)],
                (ARRAY['open','resolved'])[1 + (gs % 2)],
                now() - (gs || ' minutes')::interval,
                'Synthetic outage row number ' || gs || ' for incident-view plan testing.',
                ARRAY['core-api'],
                now(), now()
            FROM generate_series(1, :n) AS gs
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"n": n},
    )
    db.execute(text("ANALYZE outages"))
    db.commit()


class TestOutageIncidentViewUsesCompositeIndex:
    def test_incident_view_query_plan_is_not_a_seq_scan(self, db) -> None:
        # A near-empty table makes a seq scan the objectively correct
        # choice regardless of indexes, so this only means something with a
        # realistic amount of data -- hence the large fixture.
        _seed_outages(db)
        try:
            failures = check_outage_incident_view_plan(db, status="open")
        finally:
            db.rollback()
            db.execute(text("DELETE FROM outages WHERE id LIKE :prefix"), {"prefix": f"{SEED_MARKER}-%"})
            db.commit()

        assert failures == [], "\n".join(failures)

    def test_incident_view_query_shape_matches_issue(self, db) -> None:
        # Guard the premise of the index: the repository's status + window
        # query really does filter on (status, detected_at) and orders by
        # detected_at DESC -- the exact shape the composite index serves.
        start = datetime.now(UTC) - timedelta(hours=1)
        from app.repositories.outage_repository import OutageRepository

        repo = OutageRepository(db)
        result = repo.list(status="open", start_date=start, page=1, page_size=5, include_total=False)
        assert set(result) == {"items", "total", "page", "page_size", "sort_by", "sort_direction"}
        assert result["sort_by"] == "detected_at"
        assert result["sort_direction"] == "desc"
