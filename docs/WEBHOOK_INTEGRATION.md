# Webhook Integration Guide

## Overview

Webhooks enable real-time event delivery with built-in security and versioning support. This guide covers signature verification, timestamp validation, and forward-compatible event handling.

## Authentication & Signature Verification

### Signature Headers

Each webhook delivery includes explicit signature versioning metadata:

```
X-Webhook-Signature: sha256={hex_digest}
X-Webhook-Signature-Version: 1
X-Webhook-Timestamp: 2026-04-29T14:30:45.123456
```

### Current Signature Version (v1): HMAC-SHA256

**Algorithm:**
- Compute: `HMAC-SHA256(secret_key, payload_json)`
- Output: Hex-encoded digest string
- Envelope: `sha256={hex_digest_value}`

**Verification Example (Python):**

```python
import hmac
import hashlib
import json

def verify_webhook(request_body: str, signature_header: str, secret: str) -> bool:
    """Verify webhook signature using HMAC-SHA256."""
    # Extract hex digest (remove 'sha256=' prefix)
    provided_signature = signature_header.replace('sha256=', '')
    
    # Compute expected signature
    expected_signature = hmac.new(
        secret.encode(),
        request_body.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # Compare using constant-time comparison
    return hmac.compare_digest(expected_signature, provided_signature)
```

**Verification Example (Node.js):**

```javascript
const crypto = require('crypto');

function verifyWebhook(requestBody, signatureHeader, secret) {
  // Extract hex digest
  const providedSignature = signatureHeader.replace('sha256=', '');
  
  // Compute expected signature
  const expectedSignature = crypto
    .createHmac('sha256', secret)
    .update(requestBody)
    .digest('hex');
  
  // Compare using constant-time comparison
  return crypto.timingSafeEqual(
    Buffer.from(expectedSignature),
    Buffer.from(providedSignature)
  );
}
```

## Timestamp Validation Semantics

### Timestamp Specification

- **Location**: Top-level `timestamp` field in JSON payload
- **Format**: ISO 8601 with microseconds: `2026-04-29T14:30:45.123456`
- **Timezone**: UTC (Zulu time implied)
- **Immutability**: Identical across all retry attempts and signature versions
- **Duration**: Captured when event is triggered, not when delivery is attempted

### Timestamp Usage Patterns

#### 1. Idempotency Support

Detect and deduplicate retried deliveries using webhook_id + timestamp combination:

```python
# Database unique constraint or application logic
@unique
class DeliveredWebhookRecord:
    webhook_id: UUID
    timestamp: datetime
    event_type: str
    payload_hash: str
```

**Benefit**: If a webhook is retried due to network failure or crash recovery, receivers can identify and skip duplicates using timestamp + webhook_id.

#### 2. Freshness Validation (Optional)

Reject webhooks outside a configurable time window:

```python
from datetime import datetime, timedelta

def validate_webhook_freshness(
    webhook_timestamp: str,
    max_age_seconds: int = 3600,  # 1 hour grace period
) -> bool:
    """Check if webhook is within acceptable age window."""
    event_time = datetime.fromisoformat(webhook_timestamp)
    current_time = datetime.utcnow()
    age = (current_time - event_time).total_seconds()
    
    if age > max_age_seconds:
        # Log and reject as suspicious/stale
        return False
    if age < 0:
        # Clock skew or future event
        return False
    
    return True
```

**Recommended Windows**:
- Minimum: -5 seconds (account for receiver clock skew)
- Maximum: 1-24 hours (depends on application requirements)

#### 3. Audit Trails & Reconciliation

Correlate event occurrence time with delivery/processing time:

```python
def audit_webhook_delivery(delivery_record):
    """Create audit trail with timing information."""
    event_occurred = parse_iso8601(delivery_record['timestamp'])
    delivery_attempted = delivery_record['created_at']
    delivery_succeeded = delivery_record['delivered_at']
    
    latency_seconds = (delivery_attempted - event_occurred).total_seconds()
    processing_time = (delivery_succeeded - delivery_attempted).total_seconds()
    
    audit_log({
        'event_type': delivery_record['event'],
        'event_occurred': event_occurred,
        'latency': latency_seconds,
        'processing_time': processing_time,
        'delivered': delivery_succeeded is not None,
    })
```

## Signature Version Evolution

The explicit `X-Webhook-Signature-Version` header enables safe algorithm evolution:

### Forward Compatibility Strategy

1. **New Algorithm Rollout** (e.g., EdDSA to replace SHA256):
   - Deploy new signer generating both old (v1) and new (v2) signatures in parallel
   - Receivers see: `X-Webhook-Signature-Version: 2` + new header with v2 signature
   - Old receivers ignore headers they don't recognize (backward compatible)
   - New receivers validate v2 signatures

2. **Graceful Deprecation**:
   ```
   Phase 1: Deploy dual-signing (v1 + v2)
   Phase 2: Monitor adoption of v2 signature validation
   Phase 3: After 6+ months, deprecate v1 signing
   Phase 4: Remove v1 code after consumers migrate
   ```

3. **Algorithm Migration Example**:
   ```python
   # Future opportunity: add v2 when needed
   def sign_payload(secret: str, payload: str, version: int = 1) -> Tuple[str, int]:
       if version == 1:
           return sign_payload_v1(secret, payload), 1
       elif version == 2:  # Future EdDSA implementation
           return sign_payload_v2(secret, payload), 2
       else:
           raise ValueError(f"Unsupported signature version: {version}")
   ```

## Webhook Delivery Contract

### Request Format

All webhook requests use:
- **Method**: POST
- **Content-Type**: application/json
- **Timeout**: 10 seconds
- **Retries**: 3 exponential backoff attempts (30s, 120s, 600s base with jitter)

### Payload Structure

```json
{
  "schema_version": "1",
  "event": "sla.violation",
  "timestamp": "2026-04-29T14:30:45.123456",
  "data": {
    "device_id": "dev-123",
    "outage_id": "out-456",
    "severity": "high",
    "sla_violated": true
  }
}
```

### Response Contract

- **Success**: HTTP 2xx response (body ignored)
- **Transient Failure**: HTTP 5xx → retry scheduled
- **Permanent Failure**: HTTP 4xx → dead-lettered for audit
- **Timeout**: No response in 10s → retry scheduled
- **Max Retries**: 3 total attempts, then dead-letter

## Security Best Practices

1. **Secret Management**:
   - Generate cryptographically random secrets (≥32 bytes)
   - Store on receiver side in secure configuration
   - Rotate periodically (webhook `secret_version` tracks rotations)

2. **Signature Verification**:
   - Always verify signature before processing payload
   - Use constant-time comparison (timing-attack resistant)
   - Log verification failures for security audits

3. **Timestamp Validation**:
   - Validate timestamp freshness (optional but recommended)
   - Use for idempotency deduplication (required for safe retries)
   - Log discrepancies > 1 hour for investigation

4. **Network Security**:
   - Use HTTPS endpoints (enforce in production)
   - Consider mutual TLS for high-security environments
   - Monitor webhook delivery success rates and latencies

## Operational Considerations

### Dead-Letter Queue & Replay

Deliveries that fail after all retries are marked as `dead_letter` status:

```python
# Query dead-lettered webhooks for auditing
GET /webhooks/{webhook_id}/deliveries?status=dead_letter

# Manually replay a failed delivery after investigation/fix
POST /webhooks/{webhook_id}/deliveries/{delivery_id}/replay
```

### Monitoring

Track these metrics:
- Delivery success rate (by event type)
- Retry rate and backoff effectiveness
- Average delivery latency
- Dead-letter rate (indicator of persistent problems)

### Timestamp Handling in Receivers

- Parse using ISO 8601 library (don't regex parse)
- Handle microseconds/fractional seconds correctly
- Consider receiver timezone (should normalize to UTC internally)
- Store as UTC datetime (not string) for comparison

## References

- [RFC 5869: HMAC-based Key Derivation Function](https://tools.ietf.org/html/rfc5869)
- [ISO 8601 DateTime Format](https://en.wikipedia.org/wiki/ISO_8601)
- [OWASP: Timing Attack Prevention](https://owasp.org/www-community/attacks/Timing_attack)

## Event Catalogue

| Event Type | Trigger |
|------------|---------|
| `outage.created` | New outage record persisted |
| `outage.updated` | Outage status or metadata changed |
| `outage.resolved` | Outage marked resolved with MTTR |
| `sla.evaluated` | SLA outcome computed after outage resolution |
| `sla.violation` | SLA threshold breached (penalty triggered) |
| `sla.reward` | SLA met or exceeded (reward triggered) |
| `payment.initiated` | Stellar payment transaction submitted |
| `payment.confirmed` | On-chain confirmation received |
| `payment.failed` | Payment submission failed |
| `dispute.filed` | SLA dispute submitted |
| `dispute.resolved` | SLA dispute closed |

## Registering a Webhook Endpoint

```http
POST /api/v1/webhooks
Content-Type: application/json

{
  "url": "https://your-service.example.com/hooks/apexchainx",
  "events": ["sla.violation", "payment.confirmed"],
  "secret": "your-webhook-secret-min-32-chars"
}
```

Response includes a `webhook_id` used for all future management operations.

## Listing Registered Webhooks

```http
GET /api/v1/webhooks
```

Returns all registered endpoints with their event subscriptions and current status. Secrets are never returned in listing responses.

## Deleting a Webhook

```http
DELETE /api/v1/webhooks/{webhook_id}
```

Deletes the endpoint and stops all future deliveries. In-flight deliveries already queued may still complete. Returns `204 No Content` on success.

## Querying Delivery History

```http
GET /api/v1/webhooks/{webhook_id}/deliveries
```

Query parameters:
- `status` — filter by `pending`, `delivered`, `failed`, `dead_letter`
- `limit` — page size (default 50)
- `offset` — pagination offset

Each delivery record includes the event type, attempt count, response code, and timestamps.

## Webhook Idempotency

Each delivery carries a `webhook_id` + `timestamp` pair that is stable across retry attempts. Receivers should persist these pairs and skip processing if already seen:

```python
if db.exists(webhook_id=event["webhook_id"], timestamp=event["timestamp"]):
    return 200  # already processed, acknowledge without re-processing
db.mark_delivered(webhook_id=event["webhook_id"], timestamp=event["timestamp"])
process(event)
```

## Secret Rotation

Rotate a webhook secret without downtime using the `secret_version` field:

1. Generate a new secret
2. `PATCH /api/v1/webhooks/{webhook_id}` with the new secret — the backend stores both old and new for a transition window
3. Update your receiver to accept signatures from either version
4. After all in-flight deliveries using the old secret complete, remove the old secret from your receiver

The `X-Webhook-Signature-Version` header tells receivers which version was used to sign each delivery.

---

## Retry Schedule

| Attempt | Delay (base + jitter) |
|---------|----------------------|
| 1st retry | ~30 seconds |
| 2nd retry | ~120 seconds |
| 3rd retry | ~600 seconds |

After 3 failed attempts the delivery is marked `dead_letter`. No further automatic retries occur.

---

## Response Code Handling

| Receiver Response | Action |
|------------------|--------|
| `2xx` | Delivery marked `delivered`, no retry |
| `4xx` | Delivery dead-lettered immediately (permanent failure) |
| `5xx` | Retry scheduled (transient failure) |
| Timeout (>10s) | Retry scheduled |
| Connection error | Retry scheduled |

---

## Testing Your Endpoint Locally

Use [ngrok](https://ngrok.com) or [localtunnel](https://localtunnel.me) to expose a local server during development:

```bash
ngrok http 3000
# Copy the https URL and register it as your webhook endpoint
```

Then trigger an event (e.g., resolve an outage) and verify your receiver logs the delivery.
