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
