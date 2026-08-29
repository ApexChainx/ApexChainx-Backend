# Offline-First Cache Architecture

ApexChain's offline-first story spans four independent cache surfaces. This
document describes what each one stores, how long it lives, when it is
populated, and — critically — when (and whether) it is currently purged. It
exists so the purge/eviction behavior that companion issues touch (schema
versioning, logout purges, cross-user boundaries, cache caps) is decided once
instead of re-litigated per PR.

## Table of contents

- [The four cache surfaces](#the-four-cache-surfaces)
- [Hydration rules](#hydration-rules)
- [Purge points — what actually happens today](#purge-points--what-actually-happens-today)
- [Known gaps](#known-gaps)

## The four cache surfaces

### 1. IndexedDB (`persistedCache`)

Source: `src/lib/persisted-cache.ts`

- **Database:** `apexchain-cache`, version `1`, single object store
  `query-cache` keyed by `key` (a string).
- **What's stored:** arbitrary JSON-serializable payloads written by callers.
  Today the only caller is `useOutages` (see below), which writes under keys
  like `outages:{"page":1,"page_size":10,...}` — one entry per distinct
  params combination.
- **Entry shape:**
  ```ts
  interface CacheEntry<T> {
    key: string;
    data: T;
    expiresAt: number; // epoch ms; 0 = no expiry
    updatedAt: number; // epoch ms
  }
  ```
- **TTL:** `persistedCache.set(key, data, ttlMs)` defaults `ttlMs` to
  `1000 * 60 * 30` (30 minutes) when the caller omits it. `useOutages` passes
  the same 30-minute constant explicitly (`CACHE_TTL_MS`).
- **Expiry enforcement:** lazy, on read only. `persistedCache.get()` checks
  `expiresAt` against `Date.now()`; if expired it fires an async
  `persistedCache.del(key)` and returns `null` to the caller. Nothing sweeps
  expired entries proactively — an entry that is never read again is never
  removed by TTL logic.
- **API surface:** `set`, `get`, `del`, `clear`. All four exist and are
  exported, but as of this writing `del` and `clear` have **no callers
  anywhere in the codebase** — see [Known gaps](#known-gaps).

### 2. React Query cache (in-memory)

Source: `src/features/outages/hooks/useOutages.ts`, keyed via
`slaEventKeys.outages.list(...)` (`src/lib/query-keys.ts`).

- **`staleTime`: 5 minutes.** Data younger than this is served from memory
  without a background refetch.
- **`gcTime`: 10 minutes.** Once a query has no observers (e.g. the outages
  table unmounts), React Query keeps the cached entry around for this long
  before garbage-collecting it from memory. Note this is shorter than the
  IndexedDB TTL (30 min) — the in-memory copy can disappear well before the
  persisted copy expires, which is why the hydration path in `useOutages`
  exists at all.
- **`placeholderData: keepPreviousData`** — pagination doesn't flash empty
  state between pages.
- **`retry: 2`, `refetchOnWindowFocus: false`.**
- This cache lives entirely in the `QueryClient` instance created by
  `src/providers/react-query.tsx` and is lost on a full page reload (it is
  IndexedDB, not this layer, that survives reloads).

### 3. Service worker cache (`public/sw.js`)

- **Cache name:** `apexchain-static-v1` (a plain `CACHE_NAME` constant — bumping
  the string is the only versioning mechanism).
- **What's precached on `install`:** a fixed list of app-shell routes —
  `/`, `/login`, `/outages`, `/payments`, `/bulk-import`, `/webhooks` — via
  `cache.addAll(STATIC_ASSETS)`.
- **Runtime strategy:** on `fetch`, for any `GET` request whose URL does
  **not** contain `/api/` (API calls are explicitly excluded via early
  `return` and hit the network directly, uncached), the worker serves a
  stale-while-revalidate response: return the cached response immediately if
  present, and in the background fetch the network copy, cache it on success,
  and use it to satisfy the request if there was no cached copy to return
  synchronously.
- **Scope:** this cache stores whatever the browser fetches for those routes
  — i.e. the rendered/streamed HTML and any other same-URL GET responses the
  app requests while navigating them. It is not scoped per user; see
  [Known gaps](#known-gaps).

### 4. `localStorage` (adjacent, not a "query cache")

Not part of the offline-read story but worth naming since it's the fourth
piece of client persistence in the auth path: `src/providers/session.tsx`
keeps a `noc_session_seen` flag in `localStorage` (see `SESSION_FLAG_KEY`) to
distinguish "never logged in" from "logged in previously, bootstrap check
failed transiently." `clearSession()` removes this flag on logout. Auth
tokens themselves are managed by `clearTokens()`/`setTokens()` in
`src/lib/api.ts`. This surface is out of scope for this doc beyond that one
pointer.

## Hydration rules

`useOutages` (`src/features/outages/hooks/useOutages.ts`) hydrates from
IndexedDB once per mount, in a `useEffect` guarded by a `hydratedRef`:

```ts
useEffect(() => {
  if (hydratedRef.current) return;
  hydratedRef.current = true;
  // ... persistedCache.get(cacheKeyStr).then(...)
}, [queryClient, queryKey, cacheKeyStr]);
```

Two rules govern whether the hydrated value is actually applied:

1. **Non-empty guard:** the cached payload is only used if
   `cached.items.length > 0`. A cached "zero outages" result is treated the
   same as "nothing cached" and discarded (tracked separately in issue #318 —
   operators never see a legitimately-empty cached state).
2. **Once-per-mount, and only if nothing is already there:** `hydratedRef`
   ensures the IndexedDB read happens exactly once per component mount (not
   once per `queryKey` change), and even then the hydrated value is only
   written into the React Query cache via `queryClient.setQueryData` if the
   query has no existing data yet (`!existing || existing.items.length === 0`).
   In other words, hydration never clobbers data that's already live —
   including a genuinely-empty live result, so a real "no outages" state from
   the network is not overwritten after the fact.

On every **successful** network fetch (`queryFn` in the same hook), the fresh
`PaginatedOutages` payload is written back to IndexedDB via
`persistedCache.set(cacheKeyStr, data, CACHE_TTL_MS)` — but again only when
`data.items.length > 0`, the same non-empty guard applied on write.

Note the write happens before the query result is returned, so there is a
narrow window (tracked in issue #391) where `setQueryData` from a stale
hydration can race the very fetch that's supposed to supersede it.

## Purge points — what actually happens today

This is deliberately blunt: several purge points that a reader might assume
exist **do not**, as of this writing.

| Trigger | IndexedDB (`persistedCache`) | React Query cache | SW cache |
|---|---|---|---|
| **TTL expiry** | Yes, lazily on next `get()` for that key (§1 above). No proactive sweep. | Yes, via `gcTime` (10 min after last observer). | No TTL — entries live until evicted by the `activate` purge below or the browser's own storage eviction. |
| **Logout** (`clearSession()` in `src/providers/session.tsx`) | **No.** `clearSession()` calls `clearTokens()`, clears the `noc_session_seen` flag, and updates React state — it never calls `persistedCache.del`/`clear`. | **No.** The `QueryClient` is never told to clear or invalidate on logout. | N/A (SW cache isn't keyed to a session at all). |
| **Login (new session)** | **No.** Nothing clears prior entries before a new session starts writing under the same cache keys. | **No**, same as above. | N/A |
| **Schema bump** (`DB_VERSION` in `persisted-cache.ts`) | **No migration exists.** `DB_VERSION` is hard-coded to `1`. The `onupgradeneeded` handler only creates the object store if it's missing — there is no version-bump path that would invalidate entries shaped for an older schema. | N/A | N/A |
| **SW cache version bump** (editing `CACHE_NAME` in `public/sw.js`) | N/A | N/A | **Yes.** The `activate` handler deletes every cache key that doesn't match the current `CACHE_NAME`, so bumping the string (e.g. `apexchain-static-v1` → `-v2`) purges the old precache on the next activation. |
| **Per-user boundary** | **Not enforced.** Cache keys (`outages:{...params}`) are derived only from query params, never from a user id, so switching accounts in the same browser reads/writes the same IndexedDB entries. | **Not enforced**, same reasoning — `queryKey` has no user segment. | **Not enforced.** Cached page responses are keyed by URL only. |

## Known gaps

These are the gaps the table above surfaces, each tracked as its own issue so
they can be fixed and reviewed independently rather than folded into this
docs change:

- **No logout purge for IndexedDB** — issue **#355**. The persisted outage
  cache is never cleared on logout, so a subsequent user on the same browser
  can see the previous user's offline outage list until the 30-minute TTL
  lapses.
- **No logout purge for the React Query cache** — issue **#356**. Same
  failure mode, in-memory: stale cross-user data can render before the next
  session's first fetch completes.
- **No schema versioning / migration path** — issue **#317**. `DB_VERSION`
  never changes, so a backend or type shape change has no defined way to
  invalidate previously-cached entries; they're served as-is for up to the
  full 30-minute TTL after a deploy.
- **SW cache is not user-scoped** — issue **#353**. Because the service
  worker caches responses per URL regardless of session, an authenticated,
  per-user rendered page can be served from cache to a different user
  offline.
- **No connection reuse in `persistedCache`** — issue **#362**. Every
  `set`/`get`/`del`/`clear` call opens a fresh `indexedDB.open()` connection;
  this is a performance/robustness gap adjacent to (but distinct from) the
  purge semantics documented here.
- **No cache size cap** — `persistedCache.set()` has no eviction policy for
  the total number or size of entries in the `query-cache` store; nothing in
  this repo currently tracks this as a separate issue, so it's noted here as
  an open question rather than linked to a ticket.

Until #355/#356 land, do not rely on logout to clear cached outage data in
this browser — for local testing, clear site data / IndexedDB manually, or
call `persistedCache.clear()` from the console.

## See also

- `src/lib/persisted-cache.ts` — IndexedDB layer implementation.
- `src/features/outages/hooks/useOutages.ts` — hydration + write-through.
- `public/sw.js` — service worker precache and runtime strategy.
- `src/lib/query-keys.ts` — `slaEventKeys` query-key factory used by
  `useOutages`.
- Issue #416 — documentation for the query-key factory conventions
  (companion doc, not this one).
