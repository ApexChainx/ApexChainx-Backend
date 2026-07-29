# SLA Policy Configuration & Versioning

## Overview

SLA policy configuration defines thresholds, penalty rates, and reward bases
per severity level (critical, high, medium, low). Policy changes are
versioned, atomic, and fully auditable.

## Atomic Policy Publishes (#37)

### Problem

Previously, `PUT /sla/config/{severity}` updated the row and version in two
separate writes, leaving a gap where on-chain calls could read inconsistent
state. A transaction issued during the gap could route to the wrong policy,
causing silent mis-pricing.

### Solution

Policy publishes are now atomic within a single operation:

1. **Version bump**: `policy_version` is incremented atomically with the config write.
2. **Content hash**: A SHA-256 hash of the full config payload is computed and
   exposed on config endpoints for integrity verification.
3. **Optimistic concurrency**: Clients can provide an `expected_token` with
   `PUT` requests. If another concurrent update has occurred, the request
   receives a `409 Conflict`, and the client must re-fetch and retry.
4. **History table**: Every publish is logged to `sla_config_history` with
   version, content hash, timestamp, and publisher identity.

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

- `200 OK` — Publish succeeded. Returns the new config with updated version and hash.
- `409 Conflict` — Token mismatch. Another update occurred concurrently. Re-fetch and retry.
- `404 Not Found` — Unknown severity.

#### Backward-compatible update (no token)

```http
PUT /api/v1/sla/config/{severity}
```

Without `expected_token`, the update is backward-compatible (no version bump,
no history entry).

## Thresholds

| Severity | Threshold (min) | Penalty Rate ($/min) | Base Reward ($) |
|----------|-----------------|----------------------|-----------------|
| Critical | 15              | 100                  | 750             |
| High     | 30              | 50                   | 750             |
| Medium   | 60              | 25                   | 750             |
| Low      | 120             | 10                   | 600             |

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

Unique constraint: `(severity, policy_version)` — ensures no duplicate versions.

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

This allows clients to verify config integrity independently of the server.
