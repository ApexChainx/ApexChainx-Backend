"""Outage search must stay index-backed as the table grows (#578).

Migration 0028_outage_search_trgm adds pg_trgm GIN indexes on
outages(id, site_id, site_name) so `ILIKE '%term%'` searches don't have to
scan the whole table. Whether Postgres actually uses them is a cost-based
decision, and with its default random_page_cost=4 it silently prefers a full
Seq Scan for moderately selective terms -- no error, just slower as the table
grows. app/db/session.py sets random_page_cost per connection; this seeds a
large, realistic fixture and asserts the real search query plan stays
index-backed.
"""

from sqlalchemy import text

from scripts.check_performance_regression import check_outage_search_plan

FIXTURE_ROWS = 50_000
SEED_MARKER = "perfguard"


def _seed_outages(db, n: int = FIXTURE_ROWS) -> None:
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
                'Synthetic outage row number ' || gs || ' for perf testing.',
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


class TestOutageSearchUsesTrgmIndex:
    def test_search_query_plan_is_not_a_seq_scan(self, db) -> None:
        # A near-empty table makes a seq scan the objectively correct
        # choice regardless of indexes, so this only means something with a
        # realistic amount of data -- hence the large fixture.
        _seed_outages(db)
        try:
            failures = check_outage_search_plan(db, search_term="site-42")
        finally:
            db.rollback()
            db.execute(text("DELETE FROM outages WHERE id LIKE :prefix"), {"prefix": f"{SEED_MARKER}-%"})
            db.commit()

        assert failures == [], "\n".join(failures)
