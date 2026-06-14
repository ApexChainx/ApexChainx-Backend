# ApexChainx Backend

> FastAPI backend for automated SLA management, outage tracking, Stellar blockchain payments, and audit infrastructure.

This repository is the integration layer in the ApexChainx 3-repo architecture:

| Repo | Role |
|------|------|
| `apexchainx-fe` | Frontend UI |
| `apexchainx-be` | **Backend and integration layer** (this repo) |
| `apexchainx-contracts` | Soroban smart contracts |

**System flow:** `User → FE → BE → Contracts → BE → FE`

> The frontend never calls contracts directly. The backend is the sole bridge between the UI and contract execution.

## Overview

`apexchainx-be` is a FastAPI application responsible for:

- managing outages
- computing and storing SLA results
- exposing aggregation and audit endpoints
- acting as the future bridge to Soroban contracts

As of the current stabilized baseline, the backend is strongest in the outage and SLA domains. Other domains are now routed, but not all of them are equally production-ready.

## Current Stack

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL
- Alembic
- Pydantic Settings
- Celery
- HTTPX

Dependencies are declared in [requirements.txt](requirements.txt).

## Active Runtime Surface

The app entrypoint is [app/main.py](app/main.py).

Current active routes are wired through [app/api/v1/router.py](app/api/v1/router.py):

- `/health`
- `/api/v1/audit`
- `/api/v1/jobs`
- `/api/v1/outages`
- `/api/v1/sla`
- `/api/v1/sla/disputes`
- `/api/v1/auth`
- `/api/v1/payments`
- `/api/v1/webhooks`
- `/api/v1/wallets`

Module maturity on the routed runtime:

- strongest and most integration-focused: `outages`, `sla`, `audit`
- active and functional with lighter implementations: `auth`, `payments`, `wallets`
- active but operationally dependent on database or worker infrastructure: `jobs`, `webhooks`, `sla disputes`

Dormant or contributor-only paths:

- `app/services/outage_store.py` is a legacy helper and not part of the routed runtime
- local task and webhook support still depend on optional infrastructure like Redis and Celery for full behavior
- the backend contains both a local SLA execution path and a contract adapter path; `CONTRACT_EXECUTION_MODE` determines which bridge is active at runtime

## Outage And SLA Flow

The current working backend flow is:

1. create or update an outage
2. resolve the outage with `mttr_minutes`
3. calculate SLA in the backend
4. persist the resulting SLA record
5. return the outage and SLA result to the frontend

Key files:

- `app/api/v1/endpoints/outages.py`
- `app/api/v1/endpoints/sla.py`
- `app/repositories/outage_repository.py`
- `app/repositories/sla_repository.py`
- `app/services/sla/sla_calculator.py`
- `app/services/sla/config.py`

The backend now includes both a local SLA calculator and a contract adapter surface. By default it uses the local adapter mode, but the runtime is structured so contract-backed execution can be enabled through configuration.

## Project Structure

```text
apexchainx-be/
├── alembic/                 # database migration config and versions
├── app/
│   ├── api/v1/endpoints/    # FastAPI route handlers
│   ├── core/                # settings and application config
│   ├── db/                  # SQLAlchemy base and session setup
│   ├── models/              # Pydantic and ORM models
│   ├── repositories/        # DB access layer
│   ├── services/            # domain logic and utilities
│   ├── tasks/               # Celery-related modules
│   └── utils/               # helpers such as exporters
├── docs/                    # project and integration context
├── requirements.txt
└── README.md
```

## Local Setup

### Prerequisites

- Python 3.11+ recommended
- PostgreSQL
- pip
- virtual environment support

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure Environment

Create a `.env` file in the repo root using `.env.example` as a template.

```bash
cp .env.example .env
# Edit .env with your actual configuration values
```

**SECURITY WARNING**: Never commit `.env` files to version control. The `.env.example` file shows the required variables with placeholder values.

Startup validation fails fast if critical settings are malformed. In particular:

- `API_V1_PREFIX` must start with `/`
- `DATABASE_URL` must include a URL scheme
- `ALLOWED_ORIGINS` must be valid `http` or `https` origins
- `STELLAR_NETWORK` and `CONTRACT_EXECUTION_MODE` must be supported values
- when `CELERY_TASK_ALWAYS_EAGER=false`, both Celery URLs must be present

### Run Migrations

```bash
alembic upgrade head
```

### Start The API

```bash
uvicorn app.main:app --reload
```

The backend will be available at:

- `http://localhost:8000`
- Swagger docs: `http://localhost:8000/docs`
- Liveness check: `http://localhost:8000/health/liveness`
- Readiness check: `http://localhost:8000/health/readiness`
- Legacy compatibility: `http://localhost:8000/health`

## Verification Notes

As of the latest stabilization pass:

- Python modules compile cleanly
- `app.main` imports successfully
- `/health` returns `200`

To exercise outage and SLA routes meaningfully, you still need a reachable PostgreSQL instance because those routes depend on the database layer.

A new migration verification helper is available in `tests/test_verify_migrations.py` to validate the Alembic chain and ensure the current database state matches the head revision.

## Current Limitations

This backend is stabilized, but not feature-complete.

Examples:

- `auth` and `wallets` are active but currently backed by lightweight in-memory stores rather than durable identity infrastructure
- `jobs` and `webhooks` are routed, but they rely on optional worker infrastructure to be fully operational outside eager or local modes
- the contract path exists, but the default runtime still favors the local adapter mode
- documentation and contributor expectations should follow the routed API surface, not every helper or legacy module under `app/services`

## Security Guidelines

### For Contributors

**NEVER commit sensitive information**:
- API keys, secret keys, or passwords
- Private keys for any blockchain network
- Database connection strings with credentials
- JWT secrets or encryption keys
- Personal access tokens

**ALWAYS use environment variables** for:
- Database credentials
- API keys and secrets
- Blockchain private keys
- JWT signing secrets
- External service credentials

**Documentation examples** should:
- Use placeholder values clearly marked as examples
- Never include real credentials or keys
- Include security warnings where sensitive operations are discussed
- Show secure patterns (environment variables, secure key management)

### Environment Variables

The application uses the following sensitive environment variables:

```env
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/apexchainx

# Authentication
JWT_SECRET_KEY=your-jwt-secret-here

# Stellar Blockchain (if enabled)
STELLAR_POOL_SECRET_KEY=your-stellar-secret-key-here

# External Services
REDIS_URL=redis://user:password@localhost:6379
CELERY_BROKER_URL=redis://user:password@localhost:6379/0
```

**Never commit `.env` files** to version control. Use `.env.example` for documentation.

### Reporting Security Issues

If you discover a security vulnerability:
1. Do not create a public issue
2. Email security@apexchainx.com with details
3. Allow time for the issue to be addressed before public disclosure
