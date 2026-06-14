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
