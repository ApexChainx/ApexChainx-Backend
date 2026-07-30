# Threat Model — ApexChainx Backend

> **Review cadence:** Quarterly (or after any significant architectural change).  
> **Owner:** Security lead / backend tech lead.  
> **Last reviewed:** 2026-Q3

This document enumerates per-asset threats, their mitigations, and current status for the ApexChainx backend service. Use it as the authoritative reference when adding new surface area or reviewing security posture.

---

## Methodology

Each asset is analysed using the **STRIDE** model (Spoofing, Tampering, Repudiation, Information Disclosure, Denial-of-Service, Elevation of Privilege). Threats are rated **High / Medium / Low** based on likelihood × impact.

---

## Assets

### 1. REST API (FastAPI)

| # | Threat | Category | Severity | Mitigation | Status |
|---|--------|----------|----------|------------|--------|
| A-1 | Unauthenticated callers invoke protected endpoints | Spoofing | High | JWT Bearer auth enforced on all protected routes; `app/core/security.py` validates token signature and expiry | ✅ Mitigated |
| A-2 | Replay of captured JWT tokens | Spoofing | Medium | Short-lived access tokens (configurable `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`); token family revocation on refresh-token reuse | ✅ Mitigated |
| A-3 | Oversized request bodies causing memory exhaustion | DoS | Medium | `PayloadSizeMiddleware` rejects requests exceeding the configured byte limit with HTTP 413 | ✅ Mitigated |
| A-4 | CORS misconfiguration exposes API to hostile origins | Info Disclosure | Medium | `ALLOWED_ORIGINS` validated at startup; startup fails if non-http/https origins are present | ✅ Mitigated |
| A-5 | Mass-assignment via Pydantic models | Tampering | Medium | Pydantic models use explicit field declarations; no catch-all `**kwargs` in ORM writes | ✅ Mitigated |
| A-6 | Brute-force login | Spoofing | High | Per-IP and per-user rate limiting via Redis token bucket (`app/core/rate_limiter.py`); credential stuffing detection (`app/services/credential_stuffing_detector.py`) | ✅ Mitigated |
| A-7 | Missing security headers enable clickjacking / MIME sniffing | Info Disclosure | Low | `SecurityHeadersMiddleware` injects `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` | ✅ Mitigated |
| A-8 | API versioning drift causes clients to hit deprecated behaviour | Tampering | Low | `ApiVersionMiddleware` enforces version prefix; OpenAPI snapshot CI check detects schema drift | ✅ Mitigated |
| A-9 | Sensitive data in error responses | Info Disclosure | Medium | `general_exception_handler` / `http_exception_handler` normalise all errors to safe envelopes; stack traces suppressed in production | ✅ Mitigated |

---

### 2. PostgreSQL Database

| # | Threat | Category | Severity | Mitigation | Status |
|---|--------|----------|----------|------------|--------|
| B-1 | SQL injection via unsanitised input | Tampering | High | All queries use SQLAlchemy ORM parameterised statements; raw SQL via `text()` is audited and uses bound parameters | ✅ Mitigated |
| B-2 | Credential exposure in config files | Info Disclosure | High | `DATABASE_URL` read from environment only; `.env` is git-ignored; startup validation rejects missing URL scheme | ✅ Mitigated |
| B-3 | Migration chain corruption | Tampering | Medium | Alembic revision chain is linear; `test_verify_migrations.py` asserts database is at head in CI | ✅ Mitigated |
| B-4 | Audit log tampering post-write | Repudiation | High | Migration `0020_audit_immutable` applies PostgreSQL row-level triggers preventing UPDATE/DELETE on audit rows | ✅ Mitigated |
| B-5 | Unbounded query results enabling data exfiltration | Info Disclosure | Medium | Repositories use cursor-based pagination; limit caps enforced in query layer | ✅ Mitigated |
| B-6 | Connection pool exhaustion under adversarial load | DoS | Medium | SQLAlchemy engine `pool_size` and `max_overflow` configured; readiness probe detects pool saturation | ⚠️ Review pool limits in production sizing |

---

### 3. Redis (Cache + Rate Limiter)

| # | Threat | Category | Severity | Mitigation | Status |
|---|--------|----------|----------|------------|--------|
| C-1 | Unauthenticated Redis access | Spoofing | High | Redis should be deployed with `requirepass` and TLS in production; access restricted to backend service network | ⚠️ Deployment-time control — verify in prod |
| C-2 | Cache poisoning via forged keys | Tampering | Medium | Cache keys are derived deterministically from validated inputs; no user-controlled key composition | ✅ Mitigated |
| C-3 | Rate-limit bypass via key collision | EoP | Medium | Rate-limit keys include user ID and IP; tested in `tests/test_rate_limiter_redis.py` | ✅ Mitigated |
| C-4 | Redis unavailability disabling rate limiting | DoS | Medium | `fakeredis` used in tests; `RateLimiter` degrades gracefully when Redis is down (fail-open with logging) | ⚠️ Confirm fail-open vs fail-closed policy for production |
| C-5 | Sensitive session data in plain-text cache | Info Disclosure | Low | Redis stores rate-limit counters and idempotency keys only — no PII or credentials cached | ✅ Mitigated |

---

### 4. Wallet Registry and Wallet Assets

| # | Threat | Category | Severity | Mitigation | Status |
|---|--------|----------|----------|------------|--------|
| D-1 | Private key exposure in application memory | Info Disclosure | Critical | `STELLAR_POOL_SECRET_KEY` read from env; never logged, never serialised in responses; `scrubber.py` strips keys from audit events | ✅ Mitigated |
| D-2 | Unauthorised wallet registration for another entity | Spoofing | High | Wallet registration scoped to authenticated user; ownership validated before any wallet write | ✅ Mitigated |
| D-3 | Stellar address format spoofing | Spoofing | Medium | `wallet_address.py` validates Stellar address format (G… / C…) and length before persistence | ✅ Mitigated |
| D-4 | Testnet keys used on mainnet | EoP | High | `STELLAR_NETWORK` validated at startup; `check_stellar_networks.py` script and CI test enforce testnet/mainnet key separation | ✅ Mitigated |
| D-5 | In-memory wallet store lost on restart | Availability | Medium | Wallet persistence added in migration `0010_wallet_persistence`; wallet registry backed by database | ✅ Mitigated |

---

### 5. Webhook Dispatcher

| # | Threat | Category | Severity | Mitigation | Status |
|---|--------|----------|----------|------------|--------|
| E-1 | Webhook payload tampering in transit | Tampering | High | Payloads signed with HMAC-SHA256; signature version header (`X-Apex-Signature-{version}`) attached; consumers must verify before processing | ✅ Mitigated |
| E-2 | SSRF via attacker-controlled webhook URLs | EoP | High | `WebhookSSRF` guard (`tests/test_webhook_ssrf.py`) validates destination URLs against allowlist; internal RFC-1918 addresses rejected | ✅ Mitigated |
| E-3 | Webhook secret disclosure in logs | Info Disclosure | High | Secrets stored hashed; `scrubber.py` strips secret values from log output; `WebhookSecretHousekeeping` rotates and expires old secrets | ✅ Mitigated |
| E-4 | Replay of signed webhook events | Repudiation | Medium | Idempotency keys enforced on delivery; `IdempotencyMiddleware` rejects duplicate event IDs within TTL window | ✅ Mitigated |
| E-5 | Cascading failures when consumer endpoint is down | DoS | Medium | `WebhookBreaker` circuit breaker opens after consecutive failures; exponential backoff applied by Celery retry policy | ✅ Mitigated |
| E-6 | Webhook signature version downgrade | Tampering | Medium | Signature versioning enforced; migration `0016_webhook_signature_versioning` tracks version per secret; old versions can be retired | ✅ Mitigated |

---

### 6. Stellar / Soroban Bridge

| # | Threat | Category | Severity | Mitigation | Status |
|---|--------|----------|----------|------------|--------|
| F-1 | Unauthorised invocation of Soroban settlement contract | EoP | Critical | Contract bridge invoked only after SLA result is persisted and audit-logged; `CONTRACT_EXECUTION_MODE` guards local vs contract path | ✅ Mitigated |
| F-2 | Double-spend / duplicate payment submission | Tampering | High | Payment deduplication enforced at DB level (migration `0011_payment_deduplication`); idempotency key carried through to contract call | ✅ Mitigated |
| F-3 | Mainnet contract invocation in test environment | EoP | High | `STELLAR_NETWORK` env var controls network; testnet/mainnet separation tested in `test_check_stellar_networks.py` | ✅ Mitigated |
| F-4 | Transaction memo leaks internal identifiers | Info Disclosure | Low | `tx_memo.py` service constructs memos from public correlation IDs only; internal DB PKs excluded | ✅ Mitigated |
| F-5 | XDR payload injection via crafted SLA inputs | Tampering | Medium | SLA inputs validated by Pydantic models before reaching the contract adapter; field types and ranges enforced | ✅ Mitigated |
| F-6 | Contract call timeout leaves SLA in inconsistent state | Availability | Medium | Adapter returns structured error; SLA record retains `pending_settlement` flag; Celery retry policy re-attempts settlement | ⚠️ Retry limits and dead-letter handling to be hardened |

---

## Residual Risks and Open Items

| ID | Risk | Action | Owner | Target |
|----|------|--------|-------|--------|
| R-1 | Redis in production may be deployed without auth | Enforce `requirepass` + TLS in deployment runbook | Platform team | Q3 2026 |
| R-2 | Rate-limiter fail-open behaviour in Redis outage | Define and document fail-open vs fail-closed policy | Security lead | Q3 2026 |
| R-3 | Soroban settlement retry limits not formally bounded | Add dead-letter queue and alerting for permanently failed settlements | Backend lead | Q4 2026 |
| R-4 | GDPR subject-erasure path removes audit rows | Confirm legal basis for retaining audit rows post-erasure; update `gdpr.py` if needed | Legal + Backend | Q4 2026 |

---

## Review Checklist

Before marking a quarterly review complete, confirm:

- [ ] All **Critical** and **High** items remain mitigated
- [ ] New endpoints or services since last review have been added to this document
- [ ] Residual risk owners have acknowledged their items
- [ ] Threat model reflects any infrastructure changes (new Redis clusters, DB replicas, contract upgrades)
- [ ] `docs/SECURITY.md` and `SECURITY.md` are consistent with mitigations listed here

---

## References

- [docs/SECURITY.md](SECURITY.md) — public security policy and disclosure process
- [docs/STELLAR_INTEGRATION.md](STELLAR_INTEGRATION.md) — Soroban bridge architecture and key management
- [docs/WEBHOOK_INTEGRATION.md](WEBHOOK_INTEGRATION.md) — webhook signing and delivery
- [SECURITY.md](../SECURITY.md) — vulnerability reporting
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [STRIDE threat modelling](https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats)
