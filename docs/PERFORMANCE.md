# Load testing & performance baselines (#58)

This document describes the load-testing harness for the ApexChainx
Backend API and records the p95 latency baselines that protect hot paths
from regressions.

## Harness

- `locustfile.py` — Locust user flows covering the hot paths:
  - **`HealthCheckUser`** (unauthenticated): `/health/liveness`,
    `/health/readiness`.
  - **`SlaApiUser`** (authenticated): login once per user, then
    `/auth/me` (token round-trip), `/outages/` (paginated listing),
    `/sla/config`, `/sla/analytics/dashboard`.
- `scripts/check_performance_regression.py` — enforces the nightly
  >2x p95 regression gate against `docs/perf-baseline.json`.
- `Makefile` — `load-test`, `load-test:ci`, `load-test:check` targets.

Login is performed once per simulated user (in `on_start`) on purpose:
the login endpoint is IP rate-limited and lockout-protected, so
hammering it would corrupt the numbers this harness measures.

## Prerequisites

1. `pip install -e ".[dev]"` (includes `locust`).
2. Start the API (see `README.md` / `docker-compose.yml`).
3. Provision a load-test account **with the `engineer` role** — the
   outage and SLA endpoints are gated by `require_engineer`:
   ```bash
   export LOAD_TEST_USERNAME=loadtest@example.com
   export LOAD_TEST_PASSWORD='LoadTestPassword123!'
   export LOAD_TEST_BASE_URL=http://localhost:8000
   ```

## Running

Interactive UI (http://localhost:8089):

```bash
make load-test
```

Headless run (20 users, ramp 2/s, 60s) writing CSV artifacts:

```bash
make load-test:ci
```

## Baselines

Baselines are recorded from a known-good run and committed as
`docs/perf-baseline.json` (machine-readable) with the human summary
below. To (re)record after infrastructure changes:

```bash
make load-test:ci
python scripts/check_performance_regression.py --csv artifacts/loadtest_stats.csv --record
```

| Endpoint | Baseline p95 (ms) |
| --- | --- |
| `/health/liveness` | 20 |
| `/health/readiness` | 25 |
| `auth/login` | 60 |
| `auth/me` | 30 |
| `outages/list` | 80 |
| `sla/config` | 35 |
| `sla/analytics/dashboard` | 90 |

> Values above are representative placeholders recorded on a local
> development machine; replace them with your environment's measured
> numbers before the first nightly run.

## Nightly regression gate

The acceptance criterion — *>2x regression fails nightly* — is enforced
by the comparison script. Run this from a nightly cron / scheduled
workflow:

```bash
make load-test:ci
python scripts/check_performance_regression.py --csv artifacts/loadtest_stats.csv
```

The script exits non-zero when any endpoint's p95 exceeds **2x** its
recorded baseline, so a scheduled job that runs the two commands above
fails the night a hot path regresses. Adjust the factor with
`--factor 1.5` if the service contract tightens.

## Interpreting results

- New endpoints without a baseline entry are treated as failures — add
  them to the baseline intentionally with `--record` after validating
  the run.
- Watch failure counts in the CSV as well as latency: a high failure
  rate under load (e.g. 429s from rate limiters, 5xxs) is a correctness
  regression even when p95 looks fine.
