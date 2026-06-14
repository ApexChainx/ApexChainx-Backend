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
