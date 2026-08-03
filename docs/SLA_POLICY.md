# SLA Policy — Threshold Catalog

> **Single source of truth for SLO governance.**
> This catalog mirrors the runtime configuration in
> `app/services/sla/config.py` (`SLA_CONFIG`). The drift-guard test
> `tests/test_sla_policy_doc.py` fails the build whenever this document
> and the code disagree, so the catalog is kept in sync automatically
> when `sla_config` changes.

## Threshold catalog

| Severity | MTTR target (min) | Penalty rate ($/min) | Base reward ($) |
|----------|------------------:|---------------------:|----------------:|
| critical | 15                | 100                  | 750             |
| high     | 30                | 50                   | 750             |
| medium   | 60                | 25                   | 750             |
| low      | 120               | 10                   | 600             |

- **MTTR target** — the threshold in minutes below which an outage is
  considered "met". `mttr > threshold` ⇒ SLA **violated** (penalty);
  `mttr <= threshold` ⇒ SLA **met** (reward). Boundary is deterministic:
  the violation check is strict (`>`).
- **Penalty rate** — amount charged per minute of overtime, i.e.
  `penalty = (mttr - threshold) × penalty_rate`.
- **Base reward** — the reward before the performance multiplier.

## Rating tiers (reward multiplier)

For a met SLA, the reward is scaled by how much headroom remains under
the threshold (see `SLACalculator` in `app/services/sla/sla_calculator.py`):

| Performance ratio (`mttr × 100 / threshold`) | Rating      | Multiplier | Reason code     |
|----------------------------------------------|-------------|------------|-----------------|
| < 50%                                        | exceptional | 2.0        | `met_exceptional` |
| < 75%                                        | excellent   | 1.5        | `met_excellent` |
| otherwise                                    | good        | 1.0        | `met_good`      |

A violated SLA always settles with a **penalty** (`amount < 0`), the
`poor` rating and reason code `mttr_exceeded`.

## Keeping the catalog in sync (#90)

The acceptance criterion for this document is *"Document updated when
`sla_config` changes"*. Enforced by `tests/test_sla_policy_doc.py`, which
parses the catalog table above and asserts it matches `SLA_CONFIG`
exactly.

To change a threshold, penalty or reward:

1. Edit `SLA_CONFIG` in `app/services/sla/config.py`.
2. Update the **Threshold catalog** table above.
3. Run the drift guard: `pytest tests/test_sla_policy_doc.py -q`.

If the table and the code drift apart, CI fails with a message pointing
at the exact row.

---

## Atomic Policy Publishes (#37)

### Overview

SLA policy configuration defines thresholds, penalty rates, and reward
bases per severity level (critical, high, medium, low). Policy changes
are versioned, atomic, and fully auditable.

Previously, `PUT /sla/config/{severity}` updated the row and version in
two separate writes, leaving a gap where on-chain calls could read
inconsistent state. Policy publishes are now atomic within a single
operation:

1. **Version bump**: `policy_version` is incremented atomically with the
   config write.
2. **Content hash**: A SHA-256 hash of the full config payload is
   computed and exposed on config endpoints for integrity verification.
3. **Optimistic concurrency**: Clients can provide an `expected_token`
   with `PUT` requests. If another concurrent update has occurred, the
   request receives a `409 Conflict`, and the client must re-fetch and
   retry.
4. **History table**: Every publish is logged to `sla_config_history`
   with version, content hash, timestamp, and publisher identity.

### API

#### Get config with hashes

```http
GET /api/v1/sla/config?include_hashes=true
```

Response includes `policy_version` and `content_hash` per severity:

```json
{
  "critical": {
    "severity": "critical",
    "policy_version": 3,
    "threshold_minutes": 15,
    "penalty_per_minute": 100,
    "reward_base": 750,
    "content_hash": "abc123..."
  }
}
```

#### Get publish token

```http
GET /api/v1/sla/config/{severity}/token
```

Returns the current optimistic concurrency token:

```json
{
  "severity": "critical",
  "token": "f1a2b3c4..."
}
```

#### Atomic publish with token

```http
PUT /api/v1/sla/config/{severity}?expected_token=f1a2b3c4...
Content-Type: application/json

{
  "threshold_minutes": 15,
  "penalty_per_minute": 120,
  "reward_base": 800
}
```

- `200 OK` — Publish succeeded. Returns the new config with updated
  version and hash.
- `409 Conflict` — Token mismatch. Another update occurred concurrently.
  Re-fetch and retry.
- `404 Not Found` — Unknown severity.

#### Backward-compatible update (no token)

```http
PUT /api/v1/sla/config/{severity}
```

Without `expected_token`, the update is backward-compatible (no version
bump, no history entry).

## Database Schema

### `sla_config_history`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Auto-increment PK |
| `severity` | VARCHAR(20) | Severity level |
| `policy_version` | INTEGER | Monotonically increasing version |
| `threshold_minutes` | INTEGER | MTTR threshold |
| `penalty_per_minute` | INTEGER | Penalty rate ($/min) |
| `reward_base` | INTEGER | Base reward amount ($) |
| `content_hash` | VARCHAR(64) | SHA-256 of config payload |
| `published_at` | TIMESTAMPTZ | When published |
| `published_by` | VARCHAR(255) | Publisher identity |

Unique constraint: `(severity, policy_version)` — ensures no duplicate
versions.

## Content Hash

The content hash is computed as:

```
SHA-256(json.dumps({
    "severity": <severity>,
    "policy_version": <version>,
    "threshold_minutes": <threshold>,
    "penalty_per_minute": <penalty>,
    "reward_base": <reward>,
}, sort_keys=True))
```

This allows clients to verify config integrity independently of the
server.
