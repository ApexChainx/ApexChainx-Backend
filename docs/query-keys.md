# Query Key Factory Conventions

> Issue #416 — the codebase currently has two query-key factories with no
> documented guidance on which one to use. This doc is the single source of
> truth for that decision. See also [`docs/STATE.md`](./STATE.md) for the
> broader React Query patterns (`keepPreviousData`, invalidation hierarchy).

## The canonical factory

**`slaEventKeys` in [`src/lib/query-keys.ts`](../src/lib/query-keys.ts) is the
canonical, project-wide query-key factory.** New code must import and extend
it rather than declaring ad-hoc key arrays or a second factory.

```typescript
import { slaEventKeys } from "@/lib/query-keys";

useQuery({ queryKey: slaEventKeys.outages.detail(id), ... });

queryClient.invalidateQueries({ queryKey: slaEventKeys.outages.all });
```

Why this one and not `slaQueryKeys` in `src/services/sla.ts` (see
[Known divergence](#known-divergence-do-not-copy) below): `slaEventKeys` is
rooted under a single shared prefix (`"sla-events"`), which is what lets one
mutation invalidate outages, payments, disputes, and dashboard data together
(see [How invalidation works](#how-invalidation-works)). `slaQueryKeys` is
rooted under a different, narrower prefix (`"sla"`) that only covers
calculate/preview/disputes and cannot participate in that shared
invalidation. It predates `slaEventKeys` and is kept only so existing
imports of it don't break — do not add new keys to it, and prefer migrating
call sites to `slaEventKeys` when you touch them.

## How invalidation works: prefix matching

React Query's `invalidateQueries({ queryKey })` matches by **key prefix**:
it invalidates every cached query whose key array starts with the given
array (this is a TanStack React Query built-in, not custom logic in this
repo). Concretely:

```typescript
// Cached queries in the app:
["sla-events", "outages", "list", { status: "open" }]
["sla-events", "outages", "detail-1"]
["sla-events", "payments", "list", {}]

// Invalidating the root busts everything below it:
queryClient.invalidateQueries({ queryKey: slaEventKeys.all });
// -> ["sla-events"] is a prefix of all three entries above, so all three
//    are marked stale and refetched.

// Invalidating a family root only busts that family:
queryClient.invalidateQueries({ queryKey: slaEventKeys.outages.all });
// -> ["sla-events", "outages"] matches only the first two entries.
```

This is why the factory nests every family under `slaEventKeys.all`
(`["sla-events"]`): any query key built from the factory is guaranteed to
share that prefix, so invalidating the right level of the tree reaches
every dependent query without having to know about it individually.

**The corollary — and the rule that matters most in practice:** if a query's
key is *not* built from the shared factory (a literal array like
`["dashboard-metrics", filters]`, or a key rooted under a different prefix
like `slaQueryKeys`'s `["sla", ...]`), then invalidating
`slaEventKeys.*` will never touch it, no matter how obviously related the
data is. Prefix matching only helps you if the key was actually built
under the shared prefix to begin with.

## Adding a new key family

1. Add a new property to `slaEventKeys` in `src/lib/query-keys.ts`, rooted
   under the shared `"sla-events"` prefix:

   ```typescript
   export const slaEventKeys = {
     all: ["sla-events"] as const,
     // ...existing families...

     /** Webhook deliveries */
     webhooks: {
       all: ["sla-events", "webhooks"] as const,
       list: (params?: Record<string, unknown>) =>
         ["sla-events", "webhooks", "list", params] as const,
       detail: (id: string) => ["sla-events", "webhooks", id] as const,
     },
   };
   ```

2. Follow the existing shape used by `outages`, `payments`, and `disputes`:
   an `all` root, a `list(params)` for filtered/paginated queries, and a
   `detail(id)` for single-item queries. Use `as const` on every entry so
   TypeScript infers literal tuple types.

3. Use the new family's `queryKey` in every `useQuery`/`useMutation` for
   that domain, and invalidate through it — never hand-roll a parallel
   literal-array key for the same data (see
   [Known divergence](#known-divergence-do-not-copy) for what that produces
   when it goes wrong).

4. **Register every `invalidateQueries` root.** If a mutation should bust
   this family's cache, add the call to the shared invalidation flow (see
   `src/features/outages/hooks/useInvalidateOnResolve.ts` for the pattern
   used after resolving an outage) rather than leaving it to individual
   call sites to remember. A key family with no registered invalidation
   root is a silent staleness bug waiting to happen — exactly the class of
   bug in the next section.

## Known divergence (do not copy — separate issues track fixing these)

Three places in the codebase currently query with **literal key arrays that
are not built from `slaEventKeys`**, even though the data they hold is
exactly what `slaEventKeys` mutations are meant to keep fresh. Invalidating
`slaEventKeys.*` silently does **not** reach any of these — they are
documented here as a map of the hazard, not fixed here:

- **SLA config** — `src/hooks/useSlaConfig.ts` queries
  `SLA_CONFIG_KEY = ["sla", "config"]`. This happens to collide in spelling
  with `slaEventKeys.config` (`["sla-events", "config"]`) but is a different
  array under a different root, so it is invalidated by neither
  `slaEventKeys.all` nor `slaEventKeys.config`.
- **Dashboard metrics** — `src/components/dashboard/sla-dashboard-view.tsx`
  queries `["dashboard-metrics", filters]` and
  `["dashboard-metrics-compare", comparisonFilters]`, not
  `slaEventKeys.dashboard(filters)`. The outage-resolution flow in
  `useInvalidateOnResolve.ts` invalidates `slaEventKeys.dashboard()`
  believing it refreshes this view — it does not.
- **SLA disputes** — `src/components/outages/SLADisputesPanel.tsx` queries
  `["sla-disputes", outageId]`, not `slaEventKeys.disputes.detail(...)`. The
  same `useInvalidateOnResolve.ts` flow invalidates
  `slaEventKeys.disputes.all` believing it refreshes this panel — it does
  not.

Fixing each of these (migrating the literal key to the corresponding
`slaEventKeys` entry) is tracked as separate follow-up work, not part of
this documentation change. If you're picking up one of those issues, the
fix is mechanical: replace the literal array with the matching
`slaEventKeys` accessor so the query key shares the `"sla-events"` prefix,
then confirm the existing `invalidateQueries` calls in
`useInvalidateOnResolve.ts` actually refetch the view.

## Summary

| Rule | Why |
| --- | --- |
| Use `slaEventKeys` (`src/lib/query-keys.ts`) for all new query keys | It's the canonical, project-wide factory — the only one designed for cross-domain invalidation |
| Don't add new keys to `slaQueryKeys` (`src/services/sla.ts`) | Legacy, narrower-scoped factory kept only for backward compatibility |
| Never hand-roll a literal key array for data also covered by `slaEventKeys` | Breaks prefix-matching invalidation — see [Known divergence](#known-divergence-do-not-copy) |
| Every new key family needs a registered `invalidateQueries` root | An unregistered family is a silent staleness bug |
