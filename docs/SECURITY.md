# Security Overview

## Audit Log Immutability

The `audit_logs` table is enforced as **append-only** at the database level
to prevent tampering with audit records.

- A `BEFORE DELETE` trigger (`trg_audit_logs_append_only`) raises
  `EXCEPTION 'audit_log is append-only'` for any DELETE attempt.
- Writes should be performed via a dedicated `audit_writer` database role
  with `INSERT`-only permissions on `audit_logs`, configured through the
  `DATABASE_AUDIT_URL` environment variable.
- When `DATABASE_AUDIT_URL` is set, the `AuditLogService.log()` method
  automatically routes writes through the audit-specific connection;
  falling back to the primary `DATABASE_URL` otherwise.
- No `UPDATE` or `DELETE` privileges should be granted to any application
  role on this table. Schema migrations (ALTER TABLE) must use a separate
  privileged role.
# Security Guide

## OAuth 2.0 Security Model

### redirect_uri Validation
- All redirect URIs must match the allowlist byte-for-byte (exact match, no trailing slash tolerance)
- Unknown URIs return `400 invalid_redirect`
- Configured via `OAUTH_REDIRECT_URI_ALLOWLIST` in settings

### State Parameter
- Required for all OAuth authorize requests
- Stored in Redis with 10-minute TTL
- Consumed (deleted) on callback — single-use only
- Mismatched or expired state returns `400 invalid_or_expired_state`

### PKCE (Proof Key for Code Exchange)
- `code_challenge` method: S256
- `code_verifier` is compared hashed; never stored in plaintext
- Enforced for public clients (no client_secret)
