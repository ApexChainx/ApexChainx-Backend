# CacheLayer

`app.services.cache_layer.CacheLayer` provides namespaced JSON reads and writes
over Redis. It is intended for derived or repeatable data; the database or
other backing service remains authoritative.

```python
from app.services.cache_layer import CacheLayer

cache = CacheLayer(redis_client, namespace="sla", ttl_seconds=60)
cached_result = cache.get("device:period")
if cached_result is None:
    cached_result = compute_result()
    cache.set("device:period", cached_result)
```

Keys accept ASCII letters, digits, colon, underscore, dot, slash, and hyphen,
up to 200 characters. The namespace is prepended to every key. Values are JSON
encoded and expire after the configured TTL (60 seconds by default); a write
may override the TTL. Invalid keys and TTLs raise `ValueError`. Values that
cannot be JSON encoded return `False` from `set`.

Redis operations retry up to three times by default with exponential backoff.
After repeated failures, the layer temporarily skips Redis calls. Reads then
return `None`, while writes and deletions return `False`, allowing the caller
to use its backing store. Invalid JSON is treated as a miss and removed from
Redis. This is an internal service helper, so it adds no HTTP operation to the
FastAPI OpenAPI schema; API endpoints continue to be documented by the generated
`/openapi.json` and `/docs` routes.
