# API Type Synchronization Strategy

## Problem

Frontend types in `src/types/` and backend response shapes in `apexchainx-backend` evolve independently. Without a sync strategy, type drift causes silent runtime failures in service modules and view components.

## Strategy

### Source of truth

The backend (`apexchainx-backend`) owns the canonical API contract. The frontend mirrors it in `src/types/`.

### OpenAPI codegen (preferred)

When the backend exposes an OpenAPI spec (`/openapi.json`), types are generated automatically:

```bash
npm run codegen
```

This produces `src/types/api.generated.ts` with strict TypeScript types for all API schemas, paths, and operations.

#### Setup

1. Add `openapi-typescript` as a devDependency (already done).
2. Run `npm run codegen` whenever the backend spec changes.
3. The CI workflow (`.github/workflows/api-codegen-check.yml`) detects drift automatically.

#### Re-exporting generated types

Existing manual types in `src/types/*.ts` should re-export from the generated module where applicable:

```typescript
// src/types/outages.ts
export type { components } from "./api.generated";
export type Outage = components["schemas"]["Outage"];
```

### Manual sync (fallback)

Until the backend exposes an OpenAPI spec, the process is:

1. **On every backend change** that touches a response shape, the backend PR must update `src/types/` in this repo (or open a linked FE issue).
2. **High-risk models** (listed below) are reviewed on every PR that touches `src/services/` or `src/types/`.
3. **Type assertions are banned** — no `as SomeType` casts on raw API responses. Use typed generics (`api.get<T>`) so TypeScript catches shape mismatches at compile time.

### High-risk shared models

| Type | File | Backend endpoint |
|------|------|-----------------|
| `Payment` | `src/types/payment.ts` | `GET /payments/:id` |
| `Outage` | `src/types/outages.ts` | `GET /outages/:id` |
| `SLAResult` | `src/types/outages.ts` | embedded in outage resolve response |
| `OutageResolutionPayment` | `src/types/outages.ts` | `POST /outages/:id/resolve` |

### Checklist for service changes

- [ ] Does the response shape match the current backend?
- [ ] Are optional fields (`?`) correctly marked?
- [ ] Are new fields added to the type before using them in components?
- [ ] Does `npm run build` pass with no type errors?
