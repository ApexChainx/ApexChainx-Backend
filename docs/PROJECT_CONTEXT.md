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
