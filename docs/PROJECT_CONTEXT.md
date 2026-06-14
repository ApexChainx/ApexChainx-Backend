# ApexChainx — Project Context

## Purpose

ApexChainx automates SLA compliance tracking, outage resolution, and blockchain-backed financial settlements for service operators. When an outage is resolved, the platform computes whether an SLA was breached or met, then triggers a Stellar payment accordingly — without manual intervention.

## Goals

- Replace manual SLA tracking with deterministic, auditable computation
- Provide real-time outage visibility and lifecycle management
- Automate penalty and reward payments via Soroban smart contracts
- Maintain an immutable audit trail for every state change

## Scope of this Repository

`apexchainx-be` is the central API layer. It owns:
- all business logic (SLA calculation, outage lifecycle, dispute handling)
- all database interactions
- all Soroban contract interactions

The frontend (`apexchainx-fe`) and contracts (`apexchainx-contracts`) depend on this service.

## Dispute Resolution

When a calculated SLA outcome is contested, operators can file a dispute through `/api/v1/sla/disputes`. Disputes are tracked with their own audit trail and can be resolved or rejected by authorised parties. All dispute actions are audit-logged.

## Blockchain Environment

The default `STELLAR_NETWORK=testnet` setting ensures no real assets are moved during development. Switch to `mainnet` only in a production environment with audited Soroban contracts and confirmed wallet addresses.

## Development vs Production

| Setting | Development | Production |
|---------|------------|-----------|
| `STELLAR_NETWORK` | `testnet` | `mainnet` |
| `CONTRACT_EXECUTION_MODE` | `local` | `contract` |
| `CELERY_TASK_ALWAYS_EAGER` | `true` | `false` |
| Redis / Celery worker | optional | required |
| PostgreSQL | local instance | managed instance |

---

## Audit Trail Design

Every mutating operation emits an audit event through `app/services/audit_log.py`. Events are append-only. No audit record can be deleted through the API. The `correlation_id` field on each event links it to the originating HTTP request, enabling full request-to-database tracing.

---

## Scalability Considerations

- The database is the single source of truth; no shared in-memory state exists between worker processes
- Celery workers can be scaled horizontally; each task is idempotent
- The correlation middleware adds negligible overhead (UUID generation only)
- Payload size guards prevent oversized requests from reaching the database layer

---

## Future Work

- Replace in-memory `auth` and `wallet` stores with durable PostgreSQL-backed implementations
- Enable full `CONTRACT_EXECUTION_MODE=contract` path for production SLA settlements
- Expand dispute resolution workflow with email/webhook notification on state changes
- Add OpenAPI schema validation tests to CI

---

## Contract Parity

The local SLA adapter and the Soroban contract adapter are required to produce identical outcomes for the same inputs. The test suite (`tests/test_contract_parity.py`) enforces this invariant. Any divergence is a bug.

---

## SLA Severity Tiers

SLA thresholds and penalty/reward amounts are configured per severity tier. The default tiers are:

| Severity | MTTR Threshold | Penalty | Reward |
|----------|---------------|---------|--------|
| `low` | 240 min | 50 USDC | 10 USDC |
| `medium` | 120 min | 200 USDC | 50 USDC |
| `high` | 60 min | 500 USDC | 100 USDC |
| `critical` | 30 min | 1500 USDC | 300 USDC |

Values are configurable via `app/services/sla/config.py`.

---

## Security Model

- JWT tokens are signed with `JWT_SECRET_KEY` using HS256
- All financial operations require a valid JWT
- Private keys for Stellar wallets are never accepted via API — only public keys are stored
- The payload size guard prevents DoS via oversized request bodies
- Correlation IDs enable post-hoc security investigation of any request

---

## Dependency Policy

All Python dependencies are pinned to exact versions in `requirements.txt`. No version ranges are used. This ensures reproducible builds and makes dependency updates an explicit, reviewed change.

---

## Monitoring Recommendations

For production deployments, monitor:
- `/health/readiness` for database connectivity
- Celery worker queue depth (Redis list length)
- Stellar pool wallet USDC balance
- Webhook dead-letter rate
- Auth lockout rate (indicator of credential stuffing)

---

## Changelog Policy

All changes are tracked via git. The commit history on `main` is the canonical changelog. Commits follow the `type(scope): description` convention. Breaking changes are noted in the commit body.

---

## Contributor Onboarding

New contributors should:
1. Read `README.md` for setup
2. Read `CONTRIBUTING.md` for branch and commit conventions
3. Read `docs/CODEX_CONTEXT.md` for domain and architecture context
4. Run `pytest tests/` to verify a working local environment
5. Pick an issue from the backlog scoped to one domain

---

## Cross-repo API Contract

The frontend (`apexchainx-fe`) depends on the API contract defined in `docs/API.md`. Any endpoint shape change that removes or renames fields is a **breaking change** and requires:
1. A version bump in the API (new `/api/v2` prefix)
2. A corresponding update to the frontend
3. A deprecation notice on the old endpoint before removal
