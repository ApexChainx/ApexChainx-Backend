# ApexChainx Backend

![Python](https://img.shields.io/badge/python-3.11+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green) ![PostgreSQL](https://img.shields.io/badge/postgres-15+-blue) ![Stellar](https://img.shields.io/badge/Stellar-Soroban-blueviolet) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

> FastAPI backend for automated SLA management, outage tracking, Stellar blockchain payments, and audit infrastructure.

## Architecture

ApexChainx is a 3-repo monorepo split across frontend, backend, and smart contracts:

| Repo | Role |
|------|------|
| `apexchainx-fe` | Frontend UI |
| `apexchainx-be` | **Backend and integration layer** (this repo) |
| `apexchainx-contracts` | Soroban smart contracts |

**System flow:** `User → FE → BE → Contracts → BE → FE`

The frontend never calls contracts directly. The backend is the sole bridge between the UI and on-chain execution. All Soroban interactions are brokered exclusively through `apexchainx-be`.

## Overview

`apexchainx-be` is a FastAPI application that serves as the central processing layer for the ApexChainx platform.

It is responsible for:

- **Outage management** — create, update, resolve, and search outages with full lifecycle tracking
- **SLA computation** — calculate MTTR-based SLA outcomes with penalty and reward logic
- **Audit logging** — immutable audit trail with correlation IDs across all operations
- **Stellar payments** — bridge to Soroban smart contracts for SLA-triggered settlements
- **Webhook delivery** — signed, versioned event delivery with retry and idempotency support
- **Analytics** — SLA performance aggregation, trends, snapshots, and CSV/JSON exports

The `outages` and `sla` domains are the strongest and most integration-focused. Other domains (`auth`, `payments`, `wallets`, `jobs`, `webhooks`) are fully routed but vary in infrastructure depth.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Framework | FastAPI |
| ORM | SQLAlchemy |
| Database | PostgreSQL |
| Migrations | Alembic |
| Settings | Pydantic Settings |
| Task Queue | Celery |
| HTTP Client | HTTPX |
| Blockchain | Stellar / Soroban |

Dependencies are declared in [requirements.txt](requirements.txt).

## Active Routes

The app entrypoint is [app/main.py](app/main.py). Routes are wired through [app/api/v1/router.py](app/api/v1/router.py).

Current active routes:

- `/health` — liveness and readiness probes
- `/api/v1/audit` — immutable event audit log
- `/api/v1/jobs`
- `/api/v1/outages` — full outage lifecycle management
- `/api/v1/sla` — SLA computation, analytics, and exports
- `/api/v1/sla/disputes` — file, list, and resolve SLA disputes
- `/api/v1/auth`
- `/api/v1/payments`
- `/api/v1/webhooks`
- `/api/v1/wallets`

Module maturity:

- **Strongest integration**: `outages`, `sla`, `audit`
- **Active with lighter implementations**: `auth`, `payments`, `wallets`
- **Infrastructure-dependent**: `jobs`, `webhooks`, `sla/disputes` (require Redis and Celery for full behavior)

Dormant or contributor-only paths:

- `app/services/outage_store.py` — legacy helper, not part of the routed runtime
- `CONTRACT_EXECUTION_MODE` — controls whether the local SLA adapter or the Soroban contract bridge is active

## Outage and SLA Flow

The core backend lifecycle for outage resolution and SLA settlement:

1. Create or update an outage
2. Resolve the outage with `mttr_minutes`
3. Compute the SLA outcome (penalty or reward) via MTTR-based policy evaluation
4. Persist the resulting SLA record and emit an audit event
5. Optionally trigger a Stellar payment via the contract adapter
6. Return the resolved outage and SLA result to the frontend

Key implementation files:

| File | Role |
|------|------|
| `app/api/v1/endpoints/outages.py` | Outage route handlers |
| `app/api/v1/endpoints/sla.py` | SLA route handlers |
| `app/repositories/outage_repository.py` | Outage DB access |
| `app/repositories/sla_repository.py` | SLA DB access |
| `app/services/sla/sla_calculator.py` | MTTR-based SLA computation |
| `app/services/sla/config.py` | SLA policy configuration |

The backend includes both a local SLA execution path and a Soroban contract adapter. The local adapter is the default. Contract-backed execution is enabled via `CONTRACT_EXECUTION_MODE` in the environment.

## Project Structure

```text
apexchainx-be/
├── alembic/                 # database migration config and versions
├── app/
│   ├── api/v1/endpoints/    # FastAPI route handlers
│   ├── core/                # settings and application config
│   ├── db/                  # SQLAlchemy base and session setup
│   ├── middleware/          # correlation ID and payload size middleware
│   ├── models/              # Pydantic and ORM models
│   ├── repositories/        # DB access layer
│   ├── services/            # domain logic and utilities
│   ├── tasks/               # Celery task modules
│   └── utils/               # helpers such as exporters and analytics
├── docs/                    # project and integration documentation
├── tests/                   # test suite
├── requirements.txt
└── README.md
```

## Local Setup

### Prerequisites

- Python 3.11+
- PostgreSQL
- pip
- A virtual environment tool (venv or equivalent)

### Clone the Repository

```bash
git clone https://github.com/ApexChainx/ApexChainx-Backend.git
cd ApexChainx-Backend
```

### Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure Environment

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
# Edit .env with your actual configuration values
```

**SECURITY WARNING**: Never commit `.env` to version control. The `.env.example` file contains placeholder values only.

See  in the repo root for the full list of supported variables and their default values.

### Environment Validation

Startup fails fast on misconfiguration. The following rules are enforced:

- `API_V1_PREFIX` must start with `/`
- `DATABASE_URL` must include a URL scheme
- `ALLOWED_ORIGINS` must be valid `http` or `https` origins
- `STELLAR_NETWORK` and `CONTRACT_EXECUTION_MODE` must be recognised values
- When `CELERY_TASK_ALWAYS_EAGER=false`, both `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` must be present

### Run Migrations

```bash
alembic upgrade head
```

A migration verification helper is available at `tests/test_verify_migrations.py` to validate the Alembic chain and confirm the database matches the head revision.

### Start the API

```bash
uvicorn app.main:app --reload
```

The backend will be available at:

| Endpoint | URL |
|----------|-----|
| Base | `http://localhost:8000` |
| Swagger UI | `http://localhost:8000/docs` |
| Liveness | `http://localhost:8000/health/liveness` |
| Readiness | `http://localhost:8000/health/readiness` |
| Legacy health | `http://localhost:8000/health` |

## Verification Notes

As of the latest stabilization pass:

- Python modules compile cleanly
- `app.main` imports successfully
- `/health` returns `200`

To exercise outage and SLA routes meaningfully, a reachable PostgreSQL instance is required because those routes depend on the database layer.

## Current Limitations

This backend is stabilized but not feature-complete. Known limitations:

- `auth` and `wallets` are active but currently backed by lightweight in-memory stores rather than durable identity infrastructure
- `jobs` and `webhooks` are routed, but rely on optional worker infrastructure (Redis, Celery) to be fully operational outside eager or local modes
- the contract path exists, but the default runtime favors the local adapter mode
- documentation and contributor expectations should follow the routed API surface, not every helper or legacy module under `app/services`
- SLA dispute resolution requires Redis and Celery for async notification delivery

## Security Guidelines

### For Contributors

**Never commit sensitive information:**

- API keys, secret keys, or passwords
- Private keys for any blockchain network
- Database connection strings with credentials
- JWT secrets or encryption keys
- Personal access tokens

**Always use environment variables for:**

- Database credentials
- API keys and secrets
- Blockchain private keys
- JWT signing secrets
- External service credentials

**Documentation examples must:**

- Use placeholder values clearly marked as examples
- Never include real credentials or keys
- Include security warnings where sensitive operations are discussed
- Show secure patterns (environment variables, secure key management)

### Environment Variables Reference

```env
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/apexchainx
```

```env
# Authentication
JWT_SECRET_KEY=your-jwt-secret-here
```

```env
# Stellar Blockchain
STELLAR_NETWORK=testnet
STELLAR_POOL_SECRET_KEY=your-stellar-secret-key-here
CONTRACT_EXECUTION_MODE=local
```

```env
# Task Queue (required when CELERY_TASK_ALWAYS_EAGER=false)
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
CELERY_TASK_ALWAYS_EAGER=true
```

```env
# API configuration
API_V1_PREFIX=/api/v1
ALLOWED_ORIGINS=http://localhost:3000,https://app.apexchainx.com
```

### Reporting Security Issues

If you discover a security vulnerability:

1. Do not create a public issue
2. Email security@apexchainx.com with details
3. Allow time for the issue to be addressed before public disclosure

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch conventions, commit style, and PR process.

## Additional Documentation

- [docs/API.md](docs/API.md) — full API reference with request/response examples
- [docs/STELLAR_INTEGRATION.md](docs/STELLAR_INTEGRATION.md) — Soroban contract integration and payment flow
- [docs/WEBHOOK_INTEGRATION.md](docs/WEBHOOK_INTEGRATION.md) — webhook signing, delivery, and retry
- [docs/CODEX_CONTEXT.md](docs/CODEX_CONTEXT.md) — contributor context and session inventory
- [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) — high-level project positioning and goals

## Tests

The test suite lives in `tests/`. Key test files:

| File | Coverage |
|------|----------|
| `tests/test_outage_lifecycle.py` | Outage create/resolve/search lifecycle |
| `tests/test_sla_analytics.py` | SLA analytics aggregation |
| `tests/test_contract_parity.py` | Local vs contract adapter parity |
| `tests/test_webhook_signature_versioning.py` | Webhook signing and versioning |
| `tests/test_config_validation.py` | Startup configuration validation |
| `tests/test_verify_migrations.py` | Alembic migration chain integrity |
| `tests/test_analytics_export.py` | CSV and JSON export correctness |
| `tests/test_payload_guardrails.py` | Payload size middleware enforcement |

Run tests:

```bash
pytest tests/
```

## Middleware

| Middleware | File | Behaviour |
|-----------|------|-----------|
| Correlation ID | `app/middleware/correlation.py` | Injects `X-Correlation-ID` header on every request and response |
| Payload size guard | `app/middleware/payload_size.py` | Enforces a cumulative payload size limit; rejects oversized requests |

## Analytics and Exports

SLA analytics are served through `/api/v1/sla` and support:

- aggregated performance summaries
- trend windows over configurable time ranges
- point-in-time snapshots
- CSV and JSON export via `app/utils/analytics_exporter.py`

Exports are available at  and .

## Database Migrations

Migrations are managed with Alembic. The chain lives in `alembic/versions/` and covers:

- initial tables (outages, SLA results, payments)
- operational tables and audit correlation
- SLA analytics snapshots and latest-flag
- token families and auth rate limiting
- wallet persistence and payment deduplication
- webhook secret metadata and signature versioning
- job retry tracking and SLA latest backfill

Run `alembic history --verbose` to inspect the full chain with revision details. Use `tests/test_verify_migrations.py` to confirm your database is at head.

## Background Tasks

Background task modules live in `app/tasks/`:

| Module | Purpose |
|--------|---------|
| `celery_app.py` | Celery application factory |
| `sla_tasks.py` | Async SLA computation and settlement tasks |
| `webhook_tasks.py` | Async webhook delivery with retry logic |

Tasks run eagerly (in-process) when `CELERY_TASK_ALWAYS_EAGER=true`. For production use set `CELERY_TASK_ALWAYS_EAGER=false` and provide Redis URLs.

## Payments and Wallets

The `payments` and `wallets` domains handle SLA-triggered financial settlements:

- **Payments** — record and query Stellar payment transactions linked to SLA outcomes
- **Wallets** — register and resolve Stellar wallet addresses per entity
- Both are fully routed but backed by in-memory stores in the current release; persistence is planned for a future iteration.
