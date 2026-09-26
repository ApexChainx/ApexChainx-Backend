# ADR-0003: Local SLA adapter by default with Soroban fallback

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

SLA computation can run locally (Python) or on-chain via Stellar Soroban smart contracts. We need to choose a default execution mode.

## Decision

The default mode is `local_adapter` (Python-based SLA computation). Soroban (`soroban_rpc`) is available as a fallback for contracts that require on-chain verification.

## Consequences

- Development and testing are faster without requiring a Stellar testnet
- Production deployments can opt into Soroban for auditability
- `CONTRACT_EXECUTION_MODE` env var controls the switch
