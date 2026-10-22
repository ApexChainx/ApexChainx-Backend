# EventListener: subscriber delivery and retries

`app/services/event_listener.py` implements an in-process, topic-based event
bus.  Events are recorded in insertion order and fanned out synchronously to
the subscribers registered for the event's topic, plus every subscriber
registered for the wildcard topic `"*"`.

## Delivery semantics

- The event is stored **once**, before any subscriber is invoked.  Retries
  never duplicate history entries.
- Subscribers are isolated: an exception in one subscriber does not stop
  delivery to the others.
- A failing subscriber is retried according to a `RetryPolicy` before the
  event is reported as undeliverable for that subscriber only.

## Retry policy

`RetryPolicy` is a frozen dataclass with these fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `max_attempts` | `3` | Total delivery attempts per subscriber, including the first. |
| `base_delay` | `0.05` | Seconds to wait after the first failure. |
| `max_delay` | `1.0` | Upper bound for any single backoff delay. |
| `jitter` | `True` | Apply "full jitter": the delay is drawn uniformly from `[0, delay]`. |

The delay before retry *n* is `min(base_delay * 2 ** (n - 1), max_delay)`;
with jitter enabled the actual sleep is a random value in `[0, delay]` so that
many listeners do not retry in lockstep.

Invalid configuration (`max_attempts < 1`, negative delays) raises
`ValueError` at construction time.

```python
from app.services.event_listener import EventListener, RetryPolicy

listener = EventListener(
    retry_policy=RetryPolicy(max_attempts=4, base_delay=0.1, max_delay=2.0)
)
listener.subscribe("outage.created", notify_on_call)
listener.process_event({"topic": "outage.created", "payload": {"severity": "high"}})
```

## Observability

Every retry emits a `WARNING` on the `app.services.event_listener` logger with
the structured fields `topic`, `callback`, `error_type`, `attempt`,
`max_attempts` and `retry_delay_seconds`.  When retries are exhausted the
logger emits `Subscriber failed after all retry attempts` with `attempts` and
`error_type`.

`process_event` logs a `DEBUG` record carrying `subscribers_notified`,
`subscribers_delivered` and `subscribers_failed` so dispatch outcomes can be
graphed without parsing exception text.

## Testing

`EventListener` accepts an injectable `sleep` callable.  Tests pass a recorder
instead of blocking, which lets them assert the exact backoff sequence — see
`tests/test_event_listener_retry.py`.
