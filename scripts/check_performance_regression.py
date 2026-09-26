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
    parser.add_argument("--csv", required=True, help="Path to locust stats CSV (e.g. artifacts/loadtest_stats.csv)")
    parser.add_argument("--record", action="store_true", help="Record this run as the baseline instead of comparing")
    parser.add_argument(
        "--factor", type=float, default=DEFAULT_FACTOR, help="Allowed p95 regression factor (default: 2.0)"
    )
    args = parser.parse_args()

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
