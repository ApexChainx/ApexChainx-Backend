# Block Poller

`app/services/block_poller.py` provides a reusable, fault-tolerant poller for
sequential blockchain blocks. It is intentionally transport-agnostic: callers
supply a `fetch_block(sequence)` callable and a `process_block(block)` callable,
so the poller can sit in front of Horizon, a Soroban RPC endpoint, or a local
fixture.

The implementation covers the following tracked issues:

| Issue | Capability |
|-------|------------|
| #725 | Sliding-window rate limiting for upstream fetches |
| #727 | Input sanitization for untrusted block payloads |
| #728 | Small, single-purpose methods to keep cyclomatic complexity low |
| #729 | Retry with exponential backoff and jitter |

## Usage

```python
from app.services.block_poller import BlockPoller

poller = BlockPoller(
    fetch_block=horizon.get_ledger,
    process_block=store_ledger,
    start_sequence=current_cursor,
)

poller.start()          # background daemon thread
try:
    ...
finally:
    poller.stop()       # sets the stop event and joins the thread
```

For synchronous workers, call `poller.poll_once()` directly. It returns a
`PollResult` with one of two statuses:

| Status | Meaning |
|--------|---------|
| `processed` | A block was fetched, sanitized, processed, and the cursor advanced |
| `rate_limited` | The request budget was exhausted; no upstream call was made |

The next sequence is exposed as `poller.next_sequence`. If `process_block`
raises, the cursor is **not** advanced so the same block is retried on the next
cycle.

## Retry with backoff (#729)

Transient fetch failures are retried `BLOCK_POLLER_MAX_RETRIES` times after the
initial attempt. The delay for retry `n` (zero-based) is:

```text
delay = min(BLOCK_POLLER_RETRY_MAX_DELAY_SECONDS,
            BLOCK_POLLER_RETRY_BASE_DELAY_SECONDS * 2^n)
```

By default the delay is then jittered into
`[(1 - ratio) * delay, delay]` using
`BLOCK_POLLER_RETRY_JITTER_RATIO`. Jitter prevents multiple workers from
retrying in lockstep.

Validation failures are never retried: a malformed payload cannot be fixed by
trying again, so `BlockPollerValidationError` is raised immediately. Once the
retry budget is exhausted, `BlockPollerFetchError` is raised with the final
exception chained. `run_forever` logs the failure and continues with the next
cycle.

## Input sanitization (#727)

`sanitize_block_payload` validates every untrusted field before it reaches
domain code:

- Payloads must be JSON-serializable objects below
  `BLOCK_POLLER_MAX_PAYLOAD_BYTES`.
- Block sequences must be integers (or digit-only strings) in
  `[0, 2^63 - 1]`. The `sequence`, `ledger`, and `ledger_sequence` keys are
  accepted, and `sequence` is always populated in the result.
- Strings have control characters and null bytes removed and are bounded by
  `BLOCK_POLLER_MAX_STRING_LENGTH`.
- Identifier fields (`hash`, `id`, `tx_hash`, `transaction_hash`,
  `paging_token`) may only contain letters, digits, and `._:+/=-`.
- Collections are bounded by `BLOCK_POLLER_MAX_COLLECTION_ITEMS`, nesting by
  `BLOCK_POLLER_MAX_DEPTH`, and transactions by
  `BLOCK_POLLER_MAX_TRANSACTIONS`.
- Non-string mapping keys, unsupported Python types, and non-finite floats are
  rejected. Keys that collide after sanitization are rejected rather than
  silently merged.

## Rate limiting (#725)

A thread-safe sliding-window limiter protects the upstream source. At most
`BLOCK_POLLER_RATE_LIMIT_REQUESTS` fetches are permitted per
`BLOCK_POLLER_RATE_LIMIT_WINDOW_SECONDS`. When the budget is exhausted,
`poll_once()` returns `rate_limited` without calling `fetch_block`, and the
cursor is unchanged.

A custom limiter can be injected for tests or shared multi-worker budgets:

```python
from app.services.block_poller import BlockPoller, SlidingWindowRateLimiter

limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=2)
poller = BlockPoller(fetch_block, process_block, rate_limiter=limiter)
```

## Refactoring for complexity (#728)

The polling flow is split into focused steps: rate-limit admission, retry
policy, sanitization, processing, and cursor advancement. Payload validation is
similarly decomposed into mapping, sequence, value, transaction, and size
helpers. Each helper has a single responsibility, keeping the main poll loop
small and easy to reason about.

## Metrics

The poller emits the following metrics through `app.services.metrics`:

| Metric | Type | Description |
|--------|------|-------------|
| `block_poller_blocks_processed_total` | counter | Blocks processed successfully |
| `block_poller_retries_total` | counter | Fetch retries scheduled |
| `block_poller_fetch_failures_total` | counter | Fetches that exhausted the retry budget |
| `block_poller_validation_failures_total` | counter | Payloads rejected by sanitization |
| `block_poller_processing_failures_total` | counter | `process_block` raised |
| `block_poller_rate_limited_total` | counter | Cycles skipped by the rate limiter |
| `block_poller_next_sequence` | gauge | Cursor for the next poll |

## Configuration

All settings are validated at startup by `validate_critical_settings` in
`app/core/config.py` and documented in `.env.example`.

| Setting | Default | Description |
|---------|---------|-------------|
| `BLOCK_POLLER_POLL_INTERVAL_SECONDS` | `5` | Delay between poll cycles |
| `BLOCK_POLLER_MAX_RETRIES` | `3` | Retries after the initial fetch |
| `BLOCK_POLLER_RETRY_BASE_DELAY_SECONDS` | `1` | Base for exponential backoff |
| `BLOCK_POLLER_RETRY_MAX_DELAY_SECONDS` | `30` | Hard cap for any retry delay |
| `BLOCK_POLLER_RETRY_JITTER_RATIO` | `0.25` | Fraction of delay randomised |
| `BLOCK_POLLER_RATE_LIMIT_REQUESTS` | `10` | Fetches allowed per window |
| `BLOCK_POLLER_RATE_LIMIT_WINDOW_SECONDS` | `1` | Sliding-window length |
| `BLOCK_POLLER_MAX_PAYLOAD_BYTES` | `5242880` | Maximum serialized payload size |
| `BLOCK_POLLER_MAX_TRANSACTIONS` | `1000` | Maximum transactions per block |
| `BLOCK_POLLER_MAX_IDENTIFIER_LENGTH` | `256` | Maximum identifier length |
| `BLOCK_POLLER_MAX_STRING_LENGTH` | `4096` | Maximum generic string length |
| `BLOCK_POLLER_MAX_COLLECTION_ITEMS` | `10000` | Maximum items in a collection |
| `BLOCK_POLLER_MAX_DEPTH` | `8` | Maximum mapping/list nesting depth |

## Tests

`tests/test_block_poller.py` covers payload sanitization, retry timing and
exhaustion, non-retryable validation errors, rate-limit admission, cursor
advancement, processing failures, graceful stop, and configuration validation.
