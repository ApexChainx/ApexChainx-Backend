#!/usr/bin/env python3
"""Load-test regression gate for the locust harness (#58).

Compares the p95 latency recorded in a locust CSV report against the
committed baseline in ``docs/perf-baseline.json`` and exits non-zero when
any endpoint regresses beyond the configured factor (default: 2x).

This is the enforcement half of the ">2x regression fails nightly"
acceptance criterion for issue #58. Run it from cron / a nightly job
after ``make load-test:ci``.

Usage:
    # After a headless run (writes artifacts/loadtest_stats.csv):
    python scripts/check_performance_regression.py --csv artifacts/loadtest_stats.csv

    # (Re)record the baseline from a known-good run:
    python scripts/check_performance_regression.py --csv artifacts/loadtest_stats.csv --record

    # Seq-scan guard for outage search (#578) -- EXPLAINs the real query
    # OutageRepository.list() issues and fails if it's not using the
    # pg_trgm GIN index (needs DATABASE_URL pointed at a DB with a
    # realistic amount of outage data):
    python scripts/check_performance_regression.py --check-outage-search-plan

    # Index-backed guard for the primary incident view (#579) -- EXPLAINs the
    # status + detected_at window query and fails if it Seq Scans `outages`
    # instead of the composite index from migration 0032:
    python scripts/check_performance_regression.py --check-outage-incident-view-plan

Exit codes:
    0  all endpoints within the regression factor
    1  at least one endpoint exceeded the regression factor (or no baseline)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "docs" / "perf-baseline.json"
DEFAULT_FACTOR = 2.0

P95_COLUMN = "95%"

# #578: outages(id, site_id, site_name) each carry a pg_trgm GIN index
# (migration 0028_outage_search_trgm) so ILIKE '%term%' searches don't have to
# scan the whole table. Whether Postgres actually uses it is a cost-based
# decision: with the default random_page_cost=4 (a spinning-disk assumption)
# it silently prefers a full Seq Scan for moderately selective terms -- no
# error, just slower as the table grows. app/db/session.py sets a SSD-friendly
# random_page_cost per connection; this check captures the *actual* SQL that
# OutageRepository.list() issues (not a hand-copied re-implementation, which
# could drift from the real query) and fails if its EXPLAIN plan contains a
# Seq Scan on the outages table.
SEQ_SCAN_NODE = "Seq Scan"
OUTAGES_RELATION = "outages"

# #579: the dominant operator query -- "active outages in the last hour" --
# filters outages by status plus a detected_at window and orders newest-first.
# Migration 0032_outage_status_detected_at_index adds a composite btree on
# (status, detected_at DESC) so the equality + range + order-by shape is served
# by one index. Without it Postgres bitmap-combines ix_outages_status with a
# scan of the time dimension (or Seq Scans outright), which degrades exactly
# while the incident view is being paged during an incident.
INCIDENT_VIEW_RELATION = OUTAGES_RELATION


def find_seq_scans_on_relation(plan_node: dict, relation: str) -> list[dict]:
    """Recursively collect any Seq Scan nodes over `relation` in an EXPLAIN (FORMAT JSON) plan tree."""
    hits = []
    if plan_node.get("Node Type") == SEQ_SCAN_NODE and plan_node.get("Relation Name") == relation:
        hits.append(plan_node)
    for child in plan_node.get("Plans", []):
        hits.extend(find_seq_scans_on_relation(child, relation))
    return hits


def _capture_last_statement(session, run) -> tuple[str, dict]:
    """Run `run()` and capture the last raw SQL statement + params executed
    against `session`'s connection, via SQLAlchemy's cursor-execute hook.

    This lets the check EXPLAIN the exact SQL a repository method issues,
    instead of re-implementing the query by hand and risking drift from the
    real code as it changes.
    """
    from sqlalchemy import event

    captured: dict = {}

    def _on_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        captured["statement"] = statement
        captured["parameters"] = parameters

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _on_cursor_execute)
    try:
        run()
    except Exception:
        # We only need the SQL that got sent to the DB, not whatever `run()`
        # does with the result afterwards (e.g. row-to-model conversion) --
        # that can fail for reasons unrelated to the query itself.
        pass
    finally:
        event.remove(engine, "before_cursor_execute", _on_cursor_execute)

    if "statement" not in captured:
        raise RuntimeError("no SQL statement was captured -- did `run()` execute a query against this session?")
    return captured["statement"], captured["parameters"]


def check_outage_search_plan(session, search_term: str = "site-1") -> list[str]:
    """Assert that OutageRepository.list()'s search query plan uses the
    pg_trgm GIN index rather than a Seq Scan on `outages`.

    Returns a list of failure messages; empty means the check passed. The
    caller is responsible for making sure `outages` has a realistic amount of
    data and has been ANALYZEd -- on a near-empty table Postgres correctly
    (and uninterestingly) prefers a seq scan regardless of indexes.
    """
    from app.repositories.outage_repository import OutageRepository

    repo = OutageRepository(session)
    statement, params = _capture_last_statement(
        session,
        lambda: repo.list(search=search_term, page=1, page_size=20, include_total=True),
    )

    # `statement`/`params` are already in the DBAPI's native paramstyle (as
    # captured straight off `before_cursor_execute`), so this goes through
    # the raw DBAPI cursor rather than SQLAlchemy's text()/bind-param layer,
    # which expects `:name` placeholders and would mangle the `%(name)s`
    # pyformat placeholders psycopg2 uses.
    raw_conn = session.connection().connection
    cursor = raw_conn.cursor()
    try:
        cursor.execute(f"EXPLAIN (FORMAT JSON) {statement}", params)
        plan_json = cursor.fetchone()[0]
    finally:
        cursor.close()

    root = plan_json[0]["Plan"]
    seq_scans = find_seq_scans_on_relation(root, OUTAGES_RELATION)
    if seq_scans:
        return [
            f"outage search plan uses Seq Scan on '{OUTAGES_RELATION}' instead of the pg_trgm "
            f"GIN index (term={search_term!r}); plan: {json.dumps(root, indent=2)[:2000]}"
        ]
    return []


def check_outage_incident_view_plan(session, status: str = "open") -> list[str]:
    """Assert that the incident-view query plan (status + detected_at window,
    newest first) stays index-backed rather than Seq Scanning `outages` (#579).

    Runs the real OutageRepository.list() shape the incident view uses and
    EXPLAINs the exact SQL it issues (captured, not re-implemented, so the
    check cannot drift from the code). Returns a list of failure messages;
    empty means the check passed. The caller is responsible for making sure
    `outages` has a realistic amount of data and has been ANALYZEd -- on a
    near-empty table Postgres correctly (and uninterestingly) prefers a seq
    scan regardless of indexes.
    """
    from datetime import UTC, datetime, timedelta

    from app.repositories.outage_repository import OutageRepository

    repo = OutageRepository(session)
    start = datetime.now(UTC) - timedelta(days=30)
    statement, params = _capture_last_statement(
        session,
        lambda: repo.list(
            status=status,
            start_date=start,
            end_date=datetime.now(UTC),
            page=1,
            page_size=20,
            include_total=False,
        ),
    )

    raw_conn = session.connection().connection
    cursor = raw_conn.cursor()
    try:
        cursor.execute(f"EXPLAIN (FORMAT JSON) {statement}", params)
        plan_json = cursor.fetchone()[0]
    finally:
        cursor.close()

    root = plan_json[0]["Plan"]
    seq_scans = find_seq_scans_on_relation(root, INCIDENT_VIEW_RELATION)
    if seq_scans:
        return [
            f"outage incident-view plan uses Seq Scan on '{INCIDENT_VIEW_RELATION}' instead of the composite "
            f"(status, detected_at) index (status={status!r}); plan: {json.dumps(root, indent=2)[:2000]}"
        ]
    return []


def read_stats(csv_path: Path) -> dict[str, float]:
    """Map endpoint name -> p95 latency (ms) from a locust stats CSV."""
    p95_by_endpoint: dict[str, float] = {}
    with csv_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            name = row.get("Name")
            p95_raw = row.get(P95_COLUMN)
            if not name or p95_raw is None:
                continue
            try:
                p95_by_endpoint[name] = float(p95_raw)
            except ValueError:
                continue
    return p95_by_endpoint


def load_baseline() -> dict[str, float]:
    if not BASELINE_PATH.exists():
        print(f"error: no baseline found at {BASELINE_PATH}; run with --record first", file=sys.stderr)
        sys.exit(1)
    return json.loads(BASELINE_PATH.read_text())


def save_baseline(stats: dict[str, float]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
    print(f"recorded baseline for {len(stats)} endpoints -> {BASELINE_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="Path to locust stats CSV (e.g. artifacts/loadtest_stats.csv)")
    parser.add_argument("--record", action="store_true", help="Record this run as the baseline instead of comparing")
    parser.add_argument(
        "--factor", type=float, default=DEFAULT_FACTOR, help="Allowed p95 regression factor (default: 2.0)"
    )
    parser.add_argument(
        "--check-outage-search-plan",
        action="store_true",
        help="Instead of the locust CSV comparison, EXPLAIN the real outage "
        "search query and fail if it falls back to a Seq Scan on `outages` "
        "instead of the pg_trgm GIN index (#578). Requires DATABASE_URL to "
        "point at a DB with a realistic amount of outage data.",
    )
    parser.add_argument("--search-term", default="site-1", help="Search term to EXPLAIN with (default: 'site-1')")
    parser.add_argument(
        "--check-outage-incident-view-plan",
        action="store_true",
        help="Instead of the locust CSV comparison, EXPLAIN the primary "
        "incident-view query (status + detected_at window, newest first) and "
        "fail if it falls back to a Seq Scan on `outages` instead of the "
        "composite index from migration 0032 (#579). Requires DATABASE_URL "
        "to point at a DB with a realistic amount of outage data.",
    )
    parser.add_argument(
        "--incident-status", default="open", help="Status value for the incident-view plan check (default: 'open')"
    )
    args = parser.parse_args()

    if args.check_outage_search_plan:
        from app.db.session import SessionLocal

        session = SessionLocal()
        try:
            failures = check_outage_search_plan(session, search_term=args.search_term)
        finally:
            session.close()
        if failures:
            print("outage search plan check FAILED:")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print(f"outage search plan check passed: term={args.search_term!r} uses the pg_trgm GIN index.")
        return 0

    if args.check_outage_incident_view_plan:
        from app.db.session import SessionLocal

        session = SessionLocal()
        try:
            failures = check_outage_incident_view_plan(session, status=args.incident_status)
        finally:
            session.close()
        if failures:
            print("outage incident-view plan check FAILED:")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print(
            f"outage incident-view plan check passed: status={args.incident_status!r} "
            "uses the composite (status, detected_at) index."
        )
        return 0

    if not args.csv:
        print("error: --csv is required unless --check-outage-search-plan is set", file=sys.stderr)
        return 1

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"error: stats CSV not found: {csv_path}", file=sys.stderr)
        return 1

    stats = read_stats(csv_path)
    if not stats:
        print(f"error: no endpoint rows parsed from {csv_path}", file=sys.stderr)
        return 1

    if args.record:
        save_baseline(stats)
        return 0

    baseline = load_baseline()
    failures: list[str] = []
    for name, p95 in sorted(stats.items()):
        reference = baseline.get(name)
        if reference is None:
            # New endpoint with no baseline yet: flag it so the baseline
            # is refreshed intentionally (never silently pass).
            failures.append(f"{name}: p95={p95:.1f}ms has no baseline entry")
            continue
        limit = reference * args.factor
        status = "OK" if p95 <= limit else "FAIL"
        print(f"  [{status}] {name}: p95 {p95:.1f}ms (baseline {reference:.1f}ms, limit {limit:.1f}ms)")
        if p95 > limit:
            failures.append(f"{name}: p95 {p95:.1f}ms exceeds {args.factor}x baseline {reference:.1f}ms")

    if failures:
        print("\nregression check FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nregression check passed: all endpoints within the allowed factor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
