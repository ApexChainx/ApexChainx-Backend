# Error Codes

All API errors are returned as RFC 7807 Problem Details.

## Standard HTTP Error Codes

| HTTP Status | Error Code                  | Description                                      | Retryable |
|-------------|-----------------------------|--------------------------------------------------|-----------|
| 400         | `validation_error`          | Request body or parameters failed validation     | No        |
| 401         | `unauthorized`              | Missing or invalid authentication                | No        |
| 403         | `forbidden`                 | Insufficient permissions for the resource        | No        |
| 404         | `not_found`                 | The requested resource does not exist            | No        |
| 409         | `conflict`                  | Request conflicts with the current server state  | No        |
| 413         | `payload_too_large`         | Request body exceeds the maximum allowed size    | No        |
| 422         | `unprocessable_entity`      | Semantically invalid request (Pydantic validation)| No        |
| 429         | `rate_limited`              | Too many requests — retry after `Retry-After`    | Yes       |
| 500         | `transient_error`           | A temporary server-side failure                  | Yes       |
| 500         | `internal_error`            | An unexpected internal error                     | No        |

## Domain-Specific Error Codes

| HTTP Status | Error Code                        | Description                                                    | Retryable |
|-------------|-----------------------------------|----------------------------------------------------------------|-----------|
| 400         | `invalid_stellar_public_key`      | Public key does not match Stellar G… format                    | No        |
| 400         | `invalid_tx_memo`                 | Transaction memo validation failed (whitelist, format, length)  | No        |
| 400         | `invalid_webhook_url`             | Webhook URL failed validation (SSRF, private network, schema)  | No        |
| 401         | `invalid_credentials`             | Email or password is incorrect                                 | No        |
| 401         | `account_locked`                  | Account temporarily locked after too many failed attempts      | No*       |
| 401         | `token_revoked`                   | Access or refresh token has been revoked                       | No        |
| 401         | `token_expired`                   | Access token has expired; use refresh token                    | No        |
| 401         | `refresh_token_reuse`             | Refresh token was replayed — session family invalidated        | No        |
| 401         | `session_compromised`             | Token family marked compromised after replay detection         | No        |
| 403         | `api_key_revoked`                 | The API key has been revoked                                   | No        |
| 404         | `wallet_not_found`                | No wallet exists for the given user_id or public_key           | No        |
| 404         | `webhook_not_found`               | Webhook configuration not found                                | No        |
| 404         | `delivery_not_found`              | Webhook delivery record not found                              | No        |
| 409         | `wallet_already_exists`           | A wallet already exists for this user or address               | No        |
| 409         | `wallet_already_linked`           | The user or address is already linked to a different entity    | No        |
| 409         | `sla_config_concurrency`          | SLA config was modified by another request; re-fetch and retry | Yes       |
| 429         | `credential_stuffing_detected`    | Too many unique password prefixes from this IP                 | No*       |
| 503         | `circuit_breaker_open`            | Circuit breaker is open for this endpoint; deferred delivery   | Yes       |

## Domain Exception Hierarchy

```
ApexException (base)
├── ApexValidationError    HTTP 400 — invalid input
├── ApexNotFoundError      HTTP 404 — resource missing
├── ApexConflictError      HTTP 409 — duplicate / concurrency conflict
└── ApexTransientError     HTTP 500 (retryable) — transient backend failure
```

- Service code raises `ApexException` subclasses.
- Exception handlers in `app/main.py` translate them to JSON Problem Details responses.
- Bare `except Exception` in service code has been replaced with specific typed catches.

## Auth-Specific Error Codes

| HTTP Status | Error Code                     | Description                                                   | Retryable |
|-------------|---------------------------------|---------------------------------------------------------------|-----------|
| 400         | `password_policy_violation`     | Password does not meet complexity requirements                | No        |
| 401         | `oauth_state_invalid`           | OAuth state parameter missing, expired, or mismatched         | No        |
| 401         | `oauth_code_challenge_failed`   | PKCE code_verifier does not match code_challenge              | No        |

## Webhook Error Codes

| HTTP Status | Error Code                     | Description                                                   | Retryable |
|-------------|---------------------------------|---------------------------------------------------------------|-----------|
| 400         | `webhook_ssrf_blocked`          | Webhook URL targets a private/reserved network address        | No        |
| 400         | `webhook_url_blocked`           | Webhook URL not in the configured allowlist                   | No        |
| 500         | `webhook_delivery_failed`       | Delivery to endpoint failed (timeout, connection error, 5xx)  | Yes       |
| 500         | `webhook_dead_letter`           | Delivery permanently failed after max retries                 | No        |

## Settlement / SLA Error Codes

| HTTP Status | Error Code                     | Description                                                   | Retryable |
|-------------|---------------------------------|---------------------------------------------------------------|-----------|
| 400         | `sla_unknown_severity`          | Severity level is not recognised in the SLA config            | No        |
| 400         | `sla_invalid_period`            | Period format is unsupported (use YYYY-MM or YYYY-QN)         | No        |
| 409         | `sla_config_publish_conflict`   | Optimistic concurrency failure on SLA config publish          | Yes       |
| 422         | `dispute_invalid_status`        | Dispute status transition is not allowed                      | No        |
| 500         | `sla_computation_failed`        | SLA computation encountered a transient error                 | Yes       |

## Retryability Notes

- **Yes**: The client may safely retry the request after a back-off period.
- **No**: Retrying with the same parameters will produce the same error.
- **No\***: Retrying after the lockout / cooldown period has elapsed may succeed.
