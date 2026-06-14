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
