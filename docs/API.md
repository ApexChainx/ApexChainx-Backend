# ApexChainx API Documentation

Version: 1.0.0

Base URL: `http://localhost:8000` (development) | `https://api.apexchainx.com` (production)

## Table of Contents

- [Authentication](#authentication)
- [Outages](#outages)
- [Root Cause Analysis (RCA)](#root-cause-analysis-rca)
- [SLA Management](#sla-management)
- [Stellar Payments](#stellar-payments)
- [Wallet Management](#wallet-management)
- [Smart Contracts](#smart-contracts)
- [Analytics](#analytics)
- [Reports](#reports)
- [Error Handling](#error-handling)

---

## Payload Size and Input Guardrails

The API enforces strict limits on request payloads and input data to prevent abuse and ensure system stability:

### Request Body Limits
- **Maximum request body size**: 10 MB for all endpoints
- **Error response**: `413 Payload Too Large` with message "Request body too large. Maximum allowed size is 10485760 bytes."

### File Upload Limits
- **Maximum file upload size**: 10 MB (applies to `/api/v1/outages/import`)
- **Error response**: `413 Payload Too Large` with message "File exceeds 10 MB limit"

### Data Structure Limits
- **Bulk operations**: Maximum 1000 items per bulk request
- **Affected services**: Maximum 100 services per outage
- **Site name**: Maximum 255 characters
- **Description**: Maximum 5000 characters
- **Webhook name**: Maximum 255 characters
- **Webhook URL**: Maximum 2048 characters
- **Webhook events**: Maximum 50 events per webhook

### Validation Behavior
- Oversized inputs are rejected with `400 Bad Request` and descriptive error messages
- File uploads exceeding size limits fail fast during upload
- All limits are configurable via environment variables
- Validation occurs at both middleware and model levels for defense in depth

---

This document mixes current runtime endpoints with some roadmap-style descriptions. For contributor onboarding, use the following status guide first:

- active and routed: `auth`, `audit`, `jobs`, `outages`, `payments`, `sla`, `sla disputes`, `wallets`, `webhooks`
- strongest current domains: `outages`, `sla`, `audit`, analytics under `/api/v1/sla/*`
- active but lighter-weight domains: `auth`, `payments`, `wallets`
- active but operationally dependent: `jobs`, `webhooks`, `sla disputes`
- not part of the routed runtime: legacy helpers such as `app/services/outage_store.py`

Important:

- if code and this document disagree, treat the router and endpoint modules as source of truth
- some sections below still describe aspirational production behavior; they should not be read as proof that every subfeature is live today

---

## Authentication

All authenticated endpoints require a bearer token in the Authorization header:

```
Authorization: Bearer <token>
```

### POST `/api/v1/auth/login`

Authenticate user and receive access token.

Current status:

- active and routed
- currently backed by the lightweight `AuthStore` service rather than a full external auth provider

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "[REDACTED - See password policy]"
}
```

> **SECURITY NOTE**: Never use plaintext passwords in documentation, logs, or version control. The example above shows the field structure only. Always use strong, unique passwords meeting the policy requirements (min 8 chars, uppercase, lowercase, digit, special char).

**Response (200 OK):**
```json
{
  "access_token": "[JWT TOKEN - Expires in 3600s]",
  "refresh_token": "[JWT TOKEN - Rotates on use]",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": "user123",
    "email": "user@example.com",
    "role": "engineer",
    "stellar_wallet": "[Stellar Public Key - G...]"
  }
}
```

> **SECURITY NOTE**: Tokens shown above are placeholders for documentation purposes only. Real tokens are cryptographically secure, expire according to configured TTL, and should never be logged or shared. Store tokens securely on the client side (e.g., httpOnly cookies or secure storage). Never commit tokens to version control.

**Rate Limiting & Security:**
- **IP-based Rate Limiting**: Maximum 10 login attempts per IP address within a 5-minute window
- **Account Lockout**: After 5 consecutive failed login attempts, the account is locked for 15 minutes
- **Error Responses**:
  - `429 Too Many Requests`: Rate limit exceeded
  - `401 Unauthorized`: Invalid credentials or account locked
- **Lockout Reset**: Successful login resets the failed attempt counter

### POST `/api/v1/auth/register`

Register new user account.

**Request Body:**
```json
{
  "email": "newuser@example.com",
  "password": "[REDACTED - See password policy]",
  "full_name": "John Doe",
  "role": "engineer"
}
```

> **SECURITY NOTE**: Passwords must meet policy requirements. Never log, store in plaintext, or transmit passwords insecurely. The backend hashes passwords using bcrypt before storage.

**Response (201 Created):**
```json
{
  "id": "user124",
  "email": "newuser@example.com",
  "full_name": "John Doe",
  "role": "engineer",
  "created_at": "2026-01-16T10:00:00Z"
}
```

### POST `/api/v1/auth/refresh`

Refresh access token using refresh token.

**Request Body:**
```json
{
  "refresh_token": "[REFRESH TOKEN - Rotates on use]"
}
```

> **SECURITY NOTE**: Refresh tokens rotate on each use. If a refresh token is reused (replay attack), the entire session family is invalidated for security.

**Response (200 OK):**
```json
{
  "access_token": "[JWT TOKEN - Rotated]",
  "refresh_token": "[JWT TOKEN - New rotation]",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": "user123",
    "email": "user@example.com",
    "role": "engineer",
    "stellar_wallet": "[Stellar Public Key - G...]"
  }
}
```

**Rate Limiting & Security:**
- Same IP-based rate limiting as login (10 requests per 5-minute window)
- Account lockout also applies to refresh attempts for locked accounts
- Returns `429 Too Many Requests` when rate limit exceeded

---

## Outages

Current status:

- active and strongest current domain
- primary bridge for SLA execution and payout generation

### GET `/api/v1/outages`

List all outages with optional filtering.

**Query Parameters:**
- `status` (optional): Filter by status (`active`, `resolved`, `investigating`)
- `severity` (optional): Filter by severity (`critical`, `high`, `medium`, `low`)
- `start_date` (optional): Filter from date (ISO 8601)
- `end_date` (optional): Filter to date (ISO 8601)
- `site_name` (optional): Filter by site name
- `limit` (optional, default=50): Number of results
- `offset` (optional, default=0): Pagination offset

**Example Request:**
```
GET /api/v1/outages?severity=critical&status=active&limit=10
```

**Response (200 OK):**
```json
{
  "total": 45,
  "limit": 10,
  "offset": 0,
  "outages": [
    {
      "id": "OUT001",
      "site_name": "Cell Tower Alpha",
      "severity": "critical",
      "status": "active",
      "detected_at": "2026-01-16T09:30:00Z",
      "description": "Total signal loss",
      "affected_services": ["4G", "5G"],
      "assigned_to": "user123",
      "sla_status": {
        "status": "in_progress",
        "mttr_minutes": null,
        "threshold_minutes": 15,
        "time_remaining_minutes": 12
      }
    }
  ]
}
```

### GET `/api/v1/outages/{outage_id}`

Get detailed information about a specific outage.

**Response (200 OK):**
```json
{
  "id": "OUT001",
  "site_name": "Cell Tower Alpha",
  "site_id": "SITE123",
  "severity": "critical",
  "status": "resolved",
  "detected_at": "2026-01-16T09:30:00Z",
  "resolved_at": "2026-01-16T09:45:00Z",
  "description": "Total signal loss due to power failure",
  "root_cause": "UPS battery failure",
  "affected_services": ["4G", "5G"],
  "affected_subscribers": 1500,
  "assigned_to": "user123",
  "created_by": "system",
  "location": {
    "latitude": 9.082,
    "longitude": 8.675
  },
  "sla_status": {
    "status": "met",
    "mttr_minutes": 15,
    "threshold_minutes": 15,
    "penalty_amount": 0,
    "reward_amount": 750.00,
    "performance_rating": "good",
    "payment_type": "reward",
    "smart_contract_invoked": true,
    "contract_tx_hash": "abc123..."
  },
  "stellar_payment": {
    "transaction_hash": "def456...",
    "amount": 750.00,
    "asset_code": "USDC",
    "from_address": "GPOOL...",
    "to_address": "GOPS...",
    "status": "confirmed",
    "created_at": "2026-01-16T09:46:00Z",
    "confirmed_at": "2026-01-16T09:46:05Z"
  },
  "timeline": [
    {
      "timestamp": "2026-01-16T09:30:00Z",
      "event": "Outage detected",
      "user": "system"
    },
    {
      "timestamp": "2026-01-16T09:32:00Z",
      "event": "Assigned to engineer",
      "user": "admin"
    },
    {
      "timestamp": "2026-01-16T09:45:00Z",
      "event": "Outage resolved",
      "user": "user123"
    }
  ]
}
```

### POST `/api/v1/outages`

Create a new outage record.

**Request Body:**
```json
{
  "site_name": "Cell Tower Beta",
  "site_id": "SITE456",
  "severity": "high",
  "description": "Intermittent service degradation",
  "affected_services": ["4G"],
  "affected_subscribers": 500,
  "location": {
    "latitude": 9.082,
    "longitude": 8.675
  },
  "assigned_to": "user123"
}
```

**Response (201 Created):**
```json
{
  "id": "OUT002",
  "site_name": "Cell Tower Beta",
  "status": "active",
  "detected_at": "2026-01-16T10:15:00Z",
  "message": "Outage created successfully"
}
```

### PUT `/api/v1/outages/{outage_id}`

Update an existing outage.

**Request Body:**
```json
{
  "status": "resolved",
  "resolved_at": "2026-01-16T10:40:00Z",
  "root_cause": "Fiber cut",
  "resolution_notes": "Fiber repaired, services restored"
}
```

**Response (200 OK):**
```json
{
  "id": "OUT002",
  "status": "resolved",
  "message": "Outage updated successfully",
  "sla_triggered": true
}
```

---

## SLA Management

Current status:

- active and strongest current domain
- analytics endpoints under `/api/v1/sla/analytics/*` and `/api/v1/sla/performance/aggregation` are live
- runtime can execute through local adapter mode or the contract bridge depending on config

### GET `/api/v1/sla/status/{outage_id}`

Get real-time SLA status for an outage.

**Response (200 OK):**
```json
{
  "outage_id": "OUT001",
  "status": "violated",
  "mttr_minutes": 25,
  "threshold_minutes": 15,
  "severity": "critical",
  "penalty_amount": 1000.00,
  "reward_amount": 0,
  "performance_rating": "poor",
  "payment_type": "penalty",
  "smart_contract_invoked": true,
  "contract_tx_hash": "abc123...",
  "payment_executed": true,
  "payment_tx_hash": "def456..."
}
```

### POST `/api/v1/sla/calculate`

Calculate SLA for a resolved outage (triggers smart contract).

**Request Body:**
```json
{
  "outage_id": "OUT001"
}
```

**Response (200 OK):**
```json
{
  "outage_id": "OUT001",
  "sla_result": {
    "status": "violated",
    "mttr_minutes": 25,
    "threshold_minutes": 15,
    "amount": -1000.00,
    "payment_type": "penalty"
  },
  "contract_invocation": {
    "tx_hash": "abc123...",
    "status": "confirmed",
    "gas_cost_xlm": 0.001
  }
}
```

### POST `/api/v1/sla/execute-payment`

Execute payment based on SLA result.

**Request Body:**
```json
{
  "outage_id": "OUT001",
  "operator_wallet": "GOPER...",
  "ops_team_wallet": "GOPS..."
}
```

**Response (200 OK):**
```json
{
  "payment": {
    "transaction_hash": "def456...",
    "amount": 1000.00,
    "from": "GOPER...",
    "to": "GPOOL...",
    "asset": "USDC",
    "status": "pending"
  },
  "estimated_confirmation": "2026-01-16T10:45:05Z"
}
```

### GET `/api/v1/sla/configs`

Get current SLA configurations.

**Response (200 OK):**
```json
{
  "critical": {
    "threshold_minutes": 15,
    "penalty_per_minute": 100.00,
    "reward_base": 750.00
  },
  "high": {
    "threshold_minutes": 30,
    "penalty_per_minute": 50.00,
    "reward_base": 750.00
  },
  "medium": {
    "threshold_minutes": 60,
    "penalty_per_minute": 25.00,
    "reward_base": 750.00
  },
  "low": {
    "threshold_minutes": 120,
    "penalty_per_minute": 10.00,
    "reward_base": 600.00
  }
}
```

---

## Stellar Payments

### POST `/api/v1/payments/process-sla`

Process SLA-based payment for a resolved outage.

**Request Body:**
```json
{
  "outage_id": "OUT001"
}
```

**Response (200 OK):**
```json
{
  "outage_id": "OUT001",
  "sla_result": {
    "status": "met",
    "amount": 1500.00,
    "payment_type": "reward"
  },
  "payment": {
    "transaction_hash": "xyz789...",
    "amount": 1500.00,
    "from": "GPOOL...",
    "to": "GOPS...",
    "asset": "USDC",
    "status": "confirmed"
  }
}
```

### GET `/api/v1/payments/history`

Get payment transaction history.

**Query Parameters:**
- `start_date` (optional): From date
- `end_date` (optional): To date
- `type` (optional): Filter by type (`penalty`, `reward`, `manual`)
- `status` (optional): Filter by status (`pending`, `confirmed`, `failed`)
- `limit` (optional, default=50)
- `offset` (optional, default=0)

**Response (200 OK):**
```json
{
  "total": 120,
  "limit": 50,
  "offset": 0,
  "transactions": [
    {
      "id": "pay001",
      "transaction_hash": "abc123...",
      "type": "reward",
      "amount": 1500.00,
      "asset_code": "USDC",
      "from_address": "GPOOL...",
      "to_address": "GOPS...",
      "status": "confirmed",
      "outage_id": "OUT001",
      "created_at": "2026-01-16T09:46:00Z",
      "confirmed_at": "2026-01-16T09:46:05Z",
      "explorer_url": "https://stellar.expert/explorer/testnet/tx/abc123..."
    }
  ],
  "summary": {
    "total_penalties": 5000.00,
    "total_rewards": 12000.00,
    "net_amount": 7000.00
  }
}
```

---

## Wallet Management

### POST `/api/v1/wallets/create`

Create a new Stellar wallet for a user.

**Request Body:**
```json
{
  "user_id": "user123"
}
```

**Response (201 Created):**
```json
{
  "user_id": "user123",
  "public_key": "GXXX...",
  "created_at": "2026-01-16T10:00:00Z",
  "funded": false,
  "message": "Wallet created. Please fund with at least 1 XLM to activate."
}
```

**⚠️ Security Note:** Private keys are NEVER returned via API. Users must manage their own keys via wallet apps (Freighter, Albedo).

### GET `/api/v1/wallets/{user_id}`

Get wallet details for a user.

**Query Parameters:**
- `refresh` (optional, default=false): Force a live re-fetch instead of returning cached data

**Response (200 OK):**
```json
{
  "user_id": "user123",
  "public_key": "[Stellar Public Key - G...]",
  "created_at": "2026-01-16T10:00:00Z",
  "last_updated": "2026-01-16T11:00:00Z",
  "funded": true,
  "active": true,
  "trustline_ready": true,
  "cached_at": "2026-01-16T11:00:00Z",
  "cache_status": "fresh"
}
```

> **Cache Behavior (BE-033)**: Wallet endpoints return cache metadata to help frontend make informed polling decisions:
> - `cache_status`: "fresh" (within TTL), "stale" (exceeded TTL), or "live" (just refreshed)
> - `cached_at`: Timestamp when data was last cached
> - Default TTL: 60 seconds (configurable via `WALLET_CACHE_TTL_SECONDS`)
> - Use `refresh=true` to force a live re-fetch

### GET `/api/v1/wallets/{address}/balance`

Get balance for a Stellar address.

**Response (200 OK):**
```json
{
  "address": "[Stellar Public Key - G...]",
  "balances": {
    "XLM": {
      "balance": "1000.0000000",
      "asset_type": "native"
    },
    "USDC": {
      "balance": "5000.0000000",
      "asset_type": "credit_alphanum4",
      "asset_code": "USDC",
      "asset_issuer": "[USDC Issuer - G...]"
    }
  },
  "last_updated": "2026-01-16T11:05:00Z",
  "cache_status": "fresh",
  "cache_ttl_seconds": 45,
  "cached_at": "2026-01-16T11:05:00Z"
}
```

---

## Analytics

### GET `/api/v1/analytics/mttr`

Get MTTR statistics.

**Query Parameters:**
- `start_date`: Start date (ISO 8601)
- `end_date`: End date (ISO 8601)
- `severity` (optional): Filter by severity
- `group_by` (optional): Group by (`site`, `severity`, `day`, `week`)

**Response (200 OK):**
```json
{
  "period": {
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-01-16T23:59:59Z"
  },
  "overall": {
    "average_mttr_minutes": 28.5,
    "median_mttr_minutes": 22.0,
    "total_outages": 145,
    "sla_compliance_rate": 78.5
  },
  "by_severity": {
    "critical": {
      "average_mttr": 18.2,
      "count": 12,
      "sla_met": 9,
      "sla_violated": 3
    },
    "high": {
      "average_mttr": 25.8,
      "count": 35,
      "sla_met": 28,
      "sla_violated": 7
    }
  }
}
```

### GET `/api/v1/analytics/payments`

Get payment analytics.

**Query Parameters:**
- `start_date`: Start date
- `end_date`: End date
- `group_by` (optional, default=day): Group by period

**Response (200 OK):**
```json
{
  "period": {
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-01-16T23:59:59Z"
  },
  "summary": {
    "total_penalties": 15000.00,
    "total_rewards": 22500.00,
    "net_amount": 7500.00,
    "transaction_count": 145,
    "average_transaction_amount": 258.62
  },
  "trends": [
    {
      "date": "2026-01-15",
      "penalties": 1000.00,
      "rewards": 1500.00,
      "net": 500.00,
      "count": 8
    },
    {
      "date": "2026-01-16",
      "penalties": 1500.00,
      "rewards": 2000.00,
      "net": 500.00,
      "count": 10
    }
  ]
}
```

---

## Error Handling

### Error Response Format

All errors follow this structure:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid outage severity level",
    "details": {
      "field": "severity",
      "allowed_values": ["critical", "high", "medium", "low"]
    },
    "timestamp": "2026-01-16T10:30:00Z"
  }
}
```

### Common Error Codes

| Status Code | Error Code | Description |
|-------------|------------|-------------|
| 400 | `VALIDATION_ERROR` | Invalid request data |
| 401 | `UNAUTHORIZED` | Missing or invalid authentication |
| 403 | `FORBIDDEN` | Insufficient permissions |
| 404 | `NOT_FOUND` | Resource not found |
| 409 | `CONFLICT` | Resource conflict (e.g., duplicate) |
| 422 | `UNPROCESSABLE_ENTITY` | Valid syntax but semantic errors |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests |
| 500 | `INTERNAL_SERVER_ERROR` | Server error |
| 503 | `SERVICE_UNAVAILABLE` | Service temporarily unavailable |

### Stellar-Specific Errors

| Error Code | Description | Solution |
|------------|-------------|----------|
| `STELLAR_INSUFFICIENT_BALANCE` | Not enough XLM/USDC | Fund account |
| `STELLAR_NO_TRUSTLINE` | USDC trustline not established | Create trustline |
| `STELLAR_TRANSACTION_FAILED` | Transaction failed on network | Check Stellar Explorer for details |
| `STELLAR_CONTRACT_ERROR` | Smart contract execution failed | Review contract parameters |
| `STELLAR_TIMEOUT` | Transaction confirmation timeout | Resubmit transaction |

---

## Rate Limiting

API requests are rate-limited to prevent abuse:

- **Authenticated requests**: 100 requests per minute
- **Unauthenticated requests**: 20 requests per minute
- **Payment operations**: 10 requests per minute

Rate limit headers are included in responses:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1705405200
```

---

## Pagination

List endpoints support pagination:

**Request:**
```
GET /api/v1/outages?limit=20&offset=40
```

**Response includes:**
```json
{
  "total": 145,
  "limit": 20,
  "offset": 40,
  "has_next": true,
  "has_previous": true,
  "next_offset": 60,
  "previous_offset": 20,
  "results": [...]
}
```

---

## Webhooks

APEXCHAINX can send webhooks for important events:

### Webhook Events

- `outage.created`
- `outage.resolved`
- `outage.assigned`
- `sla.calculated`
- `payment.completed`
- `payment.failed`

### Webhook Payload

```json
{
  "event": "payment.completed",
  "timestamp": "2026-01-16T10:30:00Z",
  "data": {
    "outage_id": "OUT001",
    "transaction_hash": "abc123...",
    "amount": 1500.00,
    "type": "reward"
  }
}
```

Configure webhooks in the admin panel or via API.

---

## Testing

### Swagger UI

Interactive API documentation available at:
```
http://localhost:8000/docs
```

### Postman Collection

Download our Postman collection:[WIP]
```
https://github.com/OpSoll/apexchainx-be/blob/main/postman/ApexChainx-API.json
```

---

For more information, visit our [GitHub repository](https://github.com/OpSoll/apexchainx-be)

---

## Correlation ID

Every request receives an `X-Correlation-ID` response header. To trace a specific request across logs, include the same value as a request header — otherwise the backend generates one automatically.

```http
GET /api/v1/outages
X-Correlation-ID: 550e8400-e29b-41d4-a716-446655440000
```

---

## Pagination

Endpoints that return lists accept `limit` and `offset` query parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 50 | Maximum records per page |
| `offset` | integer | 0 | Number of records to skip |

Responses include `total`, `limit`, and `offset` fields for cursor reconstruction.

---

## SLA Dispute Endpoints

### POST `/api/v1/sla/disputes`

File a dispute against a computed SLA outcome.

**Request Body:**
```json
{
  "outage_id": "OUT001",
  "sla_result_id": "SLA001",
  "reason": "MTTR measurement started before engineer was notified"
}
```

**Response (201 Created):**
```json
{
  "dispute_id": "DIS001",
  "status": "open",
  "created_at": "2026-01-16T10:00:00Z"
}
```

### GET `/api/v1/sla/disputes`

List all filed disputes. Supports `status`, `limit`, and `offset` query parameters.

**Query Parameters:**
- `status` (optional): `open`, `resolved`, `rejected`
- `limit` (optional, default=50)
- `offset` (optional, default=0)

### GET `/api/v1/sla/disputes/{dispute_id}`

Retrieve a single dispute by ID. Returns full dispute record including resolution notes if resolved.

---

## Audit Endpoints

### GET `/api/v1/audit`

Return the immutable audit event log. All state-changing operations across outages, SLA, payments, and disputes are recorded here.

**Query Parameters:**
- `correlation_id` (optional): filter by correlation ID
- `event_type` (optional): filter by event type
- `start` / `end` (optional): ISO 8601 date range
- `limit` / `offset` (optional): pagination

---

## Jobs Endpoints

### GET `/api/v1/jobs`

List background job records. Each entry reflects a Celery task with retry state, status, and scheduling metadata.

### GET `/api/v1/jobs/{job_id}`

Retrieve a single job record by ID including last attempt timestamp and error detail.

---

## Rate Limiting

Auth endpoints enforce per-user rate limiting. After `AUTH_MAX_FAILED_ATTEMPTS` consecutive failures within `AUTH_RATE_LIMIT_WINDOW_SECONDS`, the account is locked for `AUTH_LOCKOUT_DURATION_MINUTES` minutes.

Locked accounts return `429 Too Many Requests` with a `Retry-After` header.

---

## Input Validation Limits Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_REQUEST_BODY_SIZE_BYTES` | 10485760 | 10 MB max body |
| `MAX_BULK_OUTAGES_COUNT` | 1000 | Bulk import cap |
| `MAX_AFFECTED_SERVICES_COUNT` | 100 | Services per outage |
| `MAX_DESCRIPTION_LENGTH` | 5000 | Outage description |
| `MAX_WEBHOOK_EVENTS_COUNT` | 50 | Events per webhook |

---

## SLA Analytics Endpoints

### GET `/api/v1/sla/analytics/dashboard`

Returns aggregated SLA performance metrics:
- total outages evaluated
- violation rate
- average MTTR
- total penalties and rewards issued

### GET `/api/v1/sla/analytics/trends`

Returns SLA performance over a rolling time window. Supports:
- `window` — `7d`, `30d`, `90d` (default `30d`)
- `granularity` — `day`, `week` (default `day`)

### GET `/api/v1/sla/performance/aggregation`

Aggregates SLA outcomes grouped by severity, region, or service. Useful for identifying systemic underperformance patterns.

---

## Wallet Endpoints

### POST `/api/v1/wallets`

Register a Stellar wallet address for an entity.

```json
{
  "entity_id": "ORG001",
  "address": "GOPS..."
}
```

Private keys are never accepted or stored by this endpoint.

### GET `/api/v1/wallets/{entity_id}`

Retrieve the registered wallet address for an entity. Returns the public key only.

---

## Health Endpoints

### GET `/health/liveness`

Returns `200 OK` when the process is alive. Used by container orchestration to detect crashes.

### GET `/health/readiness`

Returns `200 OK` when the application can serve traffic (database reachable, config loaded). Returns `503 Service Unavailable` otherwise.

### GET `/health`

Legacy compatibility check. Returns `{"status": "ok"}`.

---

## Export Endpoints

### GET `/api/v1/sla/export`

Export SLA records in CSV or JSON format.

**Query Parameters:**
- `format` — `csv` or `json` (required)
- `start` / `end` — ISO 8601 date range (optional)
- `status` — filter by SLA status (optional)

**Response**: File download with appropriate `Content-Type` header (`text/csv` or `application/json`).

---

## Snapshot Endpoints

### GET `/api/v1/sla/snapshots`

Returns point-in-time SLA performance snapshots. Each snapshot captures the state of all active outages and their SLA status at a given moment.

**Query Parameters:**
- `at` — ISO 8601 timestamp for the snapshot (optional, defaults to now)

---

## Authentication Endpoints

### POST `/api/v1/auth/login`

Authenticate and receive a JWT access token.

```json
{
  "username": "operator@example.com",
  "password": "your-password"
}
```

Returns:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

### POST `/api/v1/auth/register`

Register a new user account.

```json
{
  "username": "operator@example.com",
  "password": "secure-password-min-12-chars"
}
```

Returns `201 Created` with the new user record (excluding password).

### POST `/api/v1/auth/refresh`

Exchange a valid refresh token for a new access token.

```json
{
  "refresh_token": "eyJ..."
}
```

Implements token family rotation — a reused refresh token invalidates the entire token family.

### POST `/api/v1/auth/logout`

Invalidate the current session. Accepts the access token in the `Authorization: Bearer` header. All tokens in the same token family are revoked.

---

## Outage Bulk Import

### POST `/api/v1/outages/import`

Import multiple outage records in a single request. Maximum `MAX_BULK_OUTAGES_COUNT` records per request (default 1000).

```json
{
  "outages": [
    { "site_name": "DC-West", "severity": "high", "services_affected": ["billing"] },
    { "site_name": "DC-East", "severity": "medium", "services_affected": ["api"] }
  ]
}
```

---

## Outage Search

### GET `/api/v1/outages/search`

Full-text and filter-based outage search.

**Query Parameters:**
- `q` — keyword search across site name, description
- `severity` — `low`, `medium`, `high`, `critical`
- `status` — `open`, `resolved`
- `start` / `end` — date range for `created_at`
- `limit` / `offset` — pagination

---

## SLA Bulk Recompute

### POST `/api/v1/sla/bulk-recompute`

Trigger recomputation of SLA outcomes for a batch of outages. Useful after changing SLA policy thresholds.

```json
{
  "outage_ids": ["OUT001", "OUT002", "OUT003"]
}
```

Returns a summary of updated records and any computation errors.

---

## Common Error Codes

| HTTP Status | Meaning |
|------------|---------|
| `400 Bad Request` | Malformed request body or invalid parameters |
| `401 Unauthorized` | Missing or invalid JWT token |
| `403 Forbidden` | Valid token but insufficient permissions |
| `404 Not Found` | Resource does not exist |
| `409 Conflict` | Duplicate resource or idempotency conflict |
| `413 Payload Too Large` | Request body exceeds size limit |
| `422 Unprocessable Entity` | Pydantic validation failure |
| `429 Too Many Requests` | Rate limit exceeded |
| `503 Service Unavailable` | Database or external dependency unreachable |

---

## Request Headers

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Type: application/json` | Yes (POST/PUT) | Required for all request bodies |
| `Authorization: Bearer {token}` | Yes (protected routes) | JWT access token |
| `X-Correlation-ID` | No | Optional; generated if absent |

---

## API Versioning

All routes are prefixed with `/api/v1`. The version prefix is configurable via `API_V1_PREFIX` but must start with `/`. Future breaking changes will be introduced under `/api/v2` without removing `/api/v1`.
