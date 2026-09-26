# Property-based tests (#56)

Hypothesis-driven property tests for the SLA domain. They generate
hundreds of scenarios and assert invariants that must hold for *any*
valid input — catching the boundary cases example-driven tests miss.

## Invariants covered

| Invariant | Test |
| --- | --- |
| availability always in [0, 100] % | `test_availability_always_within_unit_interval` |
| outage-free period is 100 % available | `test_availability_is_100_percent_without_outages` |
| adding downtime never raises availability | `test_availability_never_increases_when_downtime_is_added` |
| MTTR is never negative | `test_mttr_is_never_negative` |
| no outages ⇒ MTTR is 0 | `test_mttr_is_zero_without_outages` |
| identical inputs ⇒ identical outputs | `test_availability_and_mttr_are_deterministic` |
| violated ⇔ mttr > threshold (penalty, amount < 0) | `test_calculator_penalty_vs_reward_invariants` |
| recompute with same inputs is bit-identical | `test_calculator_is_deterministic_at_fixed_seed` |
| unknown severity is rejected with ValueError | `test_calculator_rejects_unknown_severity` |

Each `@given` test runs **150 examples** (150+ scenarios per property,
far exceeding the 100+ acceptance criterion) and completes the whole
suite in well under 30 seconds.

## Determinism at a fixed seed

Every property is decorated with `@seed(FIXED_SEED)` (12345), so a CI
failure can be reproduced locally with the exact same generated
examples. You can also override the seed on the command line:

```bash
pytest tests/properties -v --hypothesis-seed=12345
```

## Counter-example caching

Hypothesis caches failing examples in its example database
(`.hypothesis/examples` at the repo root). Once a counter-example is
found it is replayed on every subsequent run until the regression is
fixed — discoveries survive across runs automatically. High-value
boundary cases are additionally pinned with `@example(...)` so they are
always exercised even when nothing has failed yet.

To clear the cache and regenerate from scratch:

```bash
rm -rf .hypothesis
```

## Running

```bash
pytest tests/properties -v
```

The suite is picked up by the normal CI test step (`pytest` in
`.github/workflows/ci.yml`); no extra wiring is required.

## Notes

- Unresolved outages are excluded from the generated scenarios on
  purpose: their MTTR/availability math uses wall-clock time
  (`datetime.now`), which is not reproducible. Everything here stays
  deterministic.
- `tests/properties/sla_scenarios.py` holds the shared Hypothesis
  strategies; add new strategies there and reuse them in new tests.
