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
