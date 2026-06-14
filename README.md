# ApexChainx Backend

> FastAPI backend for automated SLA management, outage tracking, Stellar blockchain payments, and audit infrastructure.

## Repository Architecture

ApexChainx is a 3-repo monorepo split across frontend, backend, and smart contracts:

| Repo | Role |
|------|------|
| `apexchainx-fe` | Frontend UI |
| `apexchainx-be` | **Backend and integration layer** (this repo) |
| `apexchainx-contracts` | Soroban smart contracts |

**System flow:** `User → FE → BE → Contracts → BE → FE`

The frontend never calls contracts directly. The backend is the sole bridge between the UI and on-chain execution.

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
