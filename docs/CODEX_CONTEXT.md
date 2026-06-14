# ApexChainx Backend (apexchainx-be) – Codex Context

## Overview

This repository powers the backend API for ApexChainx, an SLA automation and outage settlement platform.

It is responsible for:
- managing outages and RCA
- calculating SLA performance
- exposing analytics and audit data
- brokering contract-aware payout logic
- handling authentication, payments, wallet state, jobs, disputes, and webhooks through the API surface

---

## Tech Stack

- Framework: FastAPI
- Language: Python (3.11+)
- Database: PostgreSQL via SQLAlchemy
- Auth: lightweight in-repo auth store
- Blockchain: Soroban-aware backend bridge with configurable execution mode
- Validation: Pydantic v2
- Async: FastAPI + Celery-oriented modules

---

## Core Domains

### 1. Outage Management

Responsible for:
- creating outages
- updating outage status
- tracking resolution
- storing metadata (location, services, subscribers)

Key endpoints:
- GET /outages
- POST /outages
- PUT /outages/{id}

---

### 2. SLA System

Core business logic.

Responsible for:
- calculating MTTR
- determining SLA compliance
- triggering penalties or rewards
- invoking smart contracts

Key endpoints:
- GET /sla/status/{outage_id}
- POST /sla/calculate
- POST /sla/execute-payment

Important:
- SLA depends on severity thresholds
- Payment logic is tightly coupled with SLA

---

### 3. Payments

Responsible for:
- exposing payment records tied to SLA outcomes
- tracking transaction status
- storing transaction history

Key endpoints:
- POST /payments/process-sla
- GET /payments/history

Flow:
Outage → SLA Calculation → Smart Contract → Payment → Record stored

---

### 4. Wallet Management

Responsible for:
- creating lightweight wallet records
- retrieving balances and status
- linking wallets to users

Key endpoints:
- POST /wallets/create
- GET /wallets/{user_id}
- GET /wallets/{address}/balance

**SECURITY CRITICAL**:
- private keys are NEVER returned via API
- private keys are NEVER logged or exposed
- only public keys and balance information are accessible
- wallet operations require proper authentication

---

### 5. Analytics

Responsible for:
- MTTR calculations
- SLA compliance metrics
- payment analytics

Key endpoints:
- GET `/api/v1/sla/analytics/dashboard`
- GET `/api/v1/sla/analytics/trends`
- GET `/api/v1/sla/performance/aggregation`

---

### 6. Audit Logging

Responsible for:
- recording all state-changing operations
- correlating events via `X-Correlation-ID`
- immutable append-only log

Key endpoints:
- GET /api/v1/audit

---

### 7. Authentication

Responsible for:
- login
- registration
- JWT issuance

Key endpoints:
- POST /auth/login
- POST /auth/register

---

## Architecture

### Layered Structure

- API Layer → routes (FastAPI endpoints)
- Service Layer → business logic and adapters
- Repository Layer → SQLAlchemy interaction
- External Layer → contract bridge, Celery, and webhook integrations

### Active vs Dormant Modules

Treat the following as active routed runtime modules:

- `auth`
- `audit`
- `jobs`
- `outages`
- `payments`
- `sla`
- `sla_dispute`
- `wallets`
- `webhooks`

Treat the following as lighter-weight or environment-dependent:

- `auth` and `wallets` are functional but currently backed by in-repo stores
- `jobs` and `webhooks` depend on worker infrastructure for full operational behavior
- contract execution depends on `CONTRACT_EXECUTION_MODE` (`local` or `contract`)

Treat the following as non-routed or legacy helper paths:

- `app/services/outage_store.py`

---

## Important Business Flows

### SLA Payment Flow

1. Outage created
2. Outage resolved
3. MTTR calculated
4. SLA evaluated
5. Contract adapter or local adapter invoked
6. Payment record generated
7. Transaction stored in DB

---

## Constraints & Rules

- All monetary actions must go through the SLA system
- Payments must be idempotent
- Wallet operations must avoid private key exposure
- SLA must be deterministic and reproducible
- API responses must follow a consistent structure

---

## Security Constraints & Rules

**CRITICAL SECURITY REQUIREMENTS**:

- **Private Key Protection**: Private keys for Stellar wallets are never exposed via API responses, logs, or documentation
- **Credential Management**: All sensitive credentials (database passwords, API keys, JWT secrets) must use environment variables
- **Documentation Safety**: Examples must use placeholder values clearly marked as non-production
- **Logging Security**: Never log sensitive information including partial keys, tokens, or passwords
- **Environment Separation**: Testnet and mainnet credentials must be completely separate
- **Access Control**: All financial operations require proper authentication and authorization
- **Audit Trail**: All payment and SLA operations must be logged for audit purposes

**Documentation Standards**:
- Use `[REDACTED]` or `[EXAMPLE]` for sensitive placeholder values
- Include security warnings for any blockchain or financial operations
- Show secure patterns (environment variables, secure key management)
- Distinguish clearly between testnet and mainnet examples

---

## Known Gaps (Areas to Generate Issues)

Codex should focus on generating issues for:

### Backend Improvements
- endpoint validation consistency
- error handling standardization
- docs alignment with routed runtime
- contributor clarity around active vs dormant modules

---

## Coding Standards

- separate routes from business logic
- validate all inputs with Pydantic
- keep business logic out of controllers
- prefer reusable services and repositories
- treat the routed runtime as source of truth when docs drift

---

## Issue Generation Rules

When generating issues:

- scope each issue to ONE domain
- include:
  - title
  - description
  - acceptance criteria
  - affected modules/files
  - dependencies (Blocked By)
- prefer small, atomic tasks
- group issues into:
  - foundational
  - feature
  - integration
  - optimization

---

## Cross-Repo Dependencies

This repo depends on:

- apexchainx-fe → consumes API
- apexchainx-contracts → executes SLA logic

Important:
- any change in SLA logic may affect contracts
- any API shape change affects frontend

---

## Goal for Codex

Generate a structured backlog of issues that:

- improves backend reliability
- ensures correctness of SLA + payments
- prepares system for production scale
- maintains clean separation of concerns

---

### SLA Disputes Domain

Responsible for:
- filing and tracking disputes against SLA outcomes
- linking disputes to originating SLA records
- providing audit trail for contested settlements

Key endpoints:
- POST /api/v1/sla/disputes
- GET /api/v1/sla/disputes
- GET /api/v1/sla/disputes/{dispute_id}

---

## Key Terms

| Term | Definition |
|------|-----------|
| MTTR | Mean Time to Resolve — the primary SLA compliance metric |
| SLA | Service Level Agreement — defines penalty/reward thresholds |
| Correlation ID | UUID injected per request for cross-system tracing |
| Contract adapter | Soroban bridge activated when `CONTRACT_EXECUTION_MODE=contract` |
| Local adapter | Default in-process SLA execution path |

---

## Rate Limiting

The auth domain includes token family tracking and per-user rate limiting. Rate limit state is stored in the database via migration `0008_auth_rate_limiting`. Abuse triggers a short-lived backoff enforced in `app/core/rate_limiter.py`.

---

## Idempotency

SLA settlement and payment execution are idempotent by design. Duplicate execution attempts for the same `outage_id` are detected and return the existing record rather than creating a second payment. Payment deduplication is enforced at the database level via migration `0011_payment_deduplication`.

---

## Correlation IDs

Every request receives an `X-Correlation-ID` header injected by `app/middleware/correlation.py`. The value propagates through audit log entries, SLA records, and payment records so that the full lifecycle of any request can be reconstructed from logs alone.

---

## Middleware Stack

Requests pass through two middleware layers before reaching route handlers:

1. **Correlation middleware** (`app/middleware/correlation.py`) — injects or propagates `X-Correlation-ID`
2. **Payload size guard** (`app/middleware/payload_size.py`) — rejects bodies exceeding `MAX_REQUEST_BODY_SIZE_BYTES`

Both are applied globally to all routes.

---

## Repository Layer Conventions

- Each domain has a dedicated repository class under `app/repositories/`
- Repositories accept a SQLAlchemy `Session` and never call each other directly
- Business logic belongs in `app/services/`, not in repositories
- Route handlers call services; services call repositories

---

## Testing Conventions

- Unit tests for service logic use mocked repositories
- Integration tests hit in-memory or test-database sessions
- Config validation tests use `pytest` with monkeypatched environment variables
- All tests live under `tests/` and use `pytest` as the runner

---

## Error Response Shape

All error responses follow a consistent envelope:

```json
{
  "detail": "Human-readable error message",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

Validation errors from Pydantic return `422 Unprocessable Entity` with a `detail` array describing each field failure.

---

## Adding a New Domain

To add a new domain to the routed runtime:

1. Create `app/models/{domain}.py` — Pydantic request/response schemas
2. Create `app/models/orm/{domain}.py` — SQLAlchemy ORM model
3. Create `app/repositories/{domain}_repository.py` — DB access
4. Create `app/services/{domain}_service.py` — business logic
5. Create `app/api/v1/endpoints/{domain}.py` — FastAPI route handlers
6. Register the router in `app/api/v1/router.py`
7. Write an Alembic migration under `alembic/versions/`
8. Add tests under `tests/`

---

## SLA Calculation Detail

MTTR (Mean Time to Resolve) is computed as the difference in minutes between the outage `created_at` timestamp and its `resolved_at` timestamp. The computed MTTR is compared against severity-specific thresholds defined in `app/services/sla/config.py`:

- If MTTR ≤ threshold → **reward** outcome
- If MTTR > threshold → **penalty** outcome

Both penalty amount and reward amount are configurable per severity tier.

---

## Webhook Delivery Infrastructure

Webhook delivery is handled by `app/tasks/webhook_tasks.py` as a Celery task. When `CELERY_TASK_ALWAYS_EAGER=true`, tasks execute synchronously in-process (no Redis required). In production, set `CELERY_TASK_ALWAYS_EAGER=false` and run a Celery worker alongside the API process.

---

## Analytics Snapshot Backfill

The migration `0012_sla_latest_backfill.py` populates the `is_latest` flag on existing SLA records. This flag allows the analytics layer to efficiently query only the most recent SLA result per outage without a subquery on every request.

---

## Bulk Recompute

When SLA policy thresholds change, existing resolved outages can be recomputed in bulk via `POST /api/v1/sla/bulk-recompute`. The operation is idempotent — re-running it with the same outage IDs produces the same result. Each recompute emits an audit event.

---

## Policy Version Pinning

SLA results are stamped with the policy version that was active at computation time. This allows historical results to be compared against current policy without ambiguity. Policy version is stored on the `sla_results` table.

---

## Clock Source Normalization

All timestamps are stored and compared in UTC. The SLA calculator normalises input timestamps to UTC before computing MTTR. Clock skew between the submitting client and the server is not compensated — use NTP-synchronized clients in production.

---

## Deterministic MTTR Boundaries

MTTR boundary computation is deterministic: given the same `created_at` and `resolved_at`, the same MTTR and SLA outcome are always produced. This property enables safe recompute and contract parity testing.

---

## Outage Lifecycle States

```
created → updated → resolved
              ↓
         (SLA computed)
              ↓
     penalty or reward outcome
              ↓
     (optional) payment triggered
```

An outage can only be resolved once. Resolved outages are immutable — subsequent updates return `409 Conflict`.
