# ADR-0001: Adopt HMAC-SHA256 + versioning for webhooks

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Webhook payloads need integrity verification so receivers can trust the data. We evaluated HMAC-SHA256, Ed25519, and plain JWT approaches.

## Decision

We use HMAC-SHA256 with explicit versioning in the `X-Webhook-Signature-Version` header. This allows future algorithm upgrades without breaking existing consumers.

## Consequences

- Receivers must validate the signature against the shared secret
- Versioning header enables smooth migration to future signature algorithms
- Simpler than asymmetric key management for the initial use case
