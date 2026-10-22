Overview
ApexChainx is a 3-repo

apexchainx-be is a FastAPI application that serves as the central processing layer for the ApexChainx platform.

It is responsible for:

Outage management — create, update, resolve, and search outages with full lifecycle tracking
SLA computation — calculate MTTR-based SLA outcomes with penalty and reward logic
Audit logging — immutable audit trail with correlation IDs across all operations
Stellar payments — bridge to Soroban smart contracts for SLA-triggered settlements
Webhook delivery — signed, versioned event delivery with retry and idempotency support
Analytics — SLA performance aggregation, trends, snapshots, and CSV/JSON exports
The outages and sla domains are the strongest and most integration-focused. The audit domain records all state-changing operations immutably. Other domains (auth, payments, wallets, jobs, webhooks) are fully routed but vary in infrastructure depth.
