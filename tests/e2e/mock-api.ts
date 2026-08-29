import type { Page } from "@playwright/test";

/**
 * Playwright route-based mock for the ApexChain backend.
 *
 * The frontend calls `http://localhost:8000/api/v1/**` from the browser, so we
 * intercept those requests at the network layer with `page.route()` and fulfil
 * them with in-memory fixtures. This keeps the E2E suite self-contained (no
 * external backend) and avoids the cross-origin/CORS issues that a separate
 * Node mock process would introduce.
 */

const TX_HASH =
  "0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9";
const FROM_ADDRESS = "GBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB";
const TO_ADDRESS = "GCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC";

const MOCK_USER = {
  id: "user-1",
  email: "ops@example.com",
  role: "admin",
  full_name: "Ops User",
};

interface RegisteredUser {
  id: string;
  email: string;
  password: string;
  full_name?: string;
  role: string;
}

/** Users created via POST /auth/register; login accepts them too. */
const registeredUsers: RegisteredUser[] = [];

interface BulkImportErrorRecord {
  row?: number;
  field?: string;
  message: string;
}

interface BulkImportHistoryRecord {
  id: string;
  filename: string;
  imported: number;
  skipped: number;
  error_count: number;
  errors: BulkImportErrorRecord[];
  created_at: string;
}

/** Records created via POST /outages/bulk; read back by the history page. */
const bulkImportHistory: BulkImportHistoryRecord[] = [];

interface SlaRecord {
  status: "met" | "violated";
  mttr_minutes: number;
  threshold_minutes: number;
  amount: number;
  payment_type: "reward" | "penalty";
  rating: string;
}

interface OutageRecord {
  id: string;
  site_name: string;
  site_id?: string;
  severity: string;
  status: "open" | "resolved";
  detected_at: string;
  description: string;
  affected_services: string[];
  affected_subscribers?: number;
  assigned_to?: string;
  root_cause?: string;
  resolution_notes?: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  sla_status: SlaRecord | null;
}

const outages: OutageRecord[] = [
  {
    id: "OUT-001",
    site_name: "Lagos Node 1",
    site_id: "site-001",
    severity: "high",
    status: "open",
    detected_at: "2026-07-28T08:00:00.000Z",
    description: "Primary transit link is degraded.",
    affected_services: ["DNS", "VoIP"],
    affected_subscribers: 1200,
    assigned_to: "Tolu",
    root_cause: "Fiber cut",
    resolution_notes: "",
    created_at: "2026-07-28T08:00:00.000Z",
    updated_at: "2026-07-28T08:00:00.000Z",
    resolved_at: null,
    sla_status: null,
  },
];

const payments = [
  {
    id: "PAY-001",
    outage_id: "OUT-001",
    amount: 125,
    asset_code: "USDC",
    type: "reward",
    status: "completed",
    transaction_hash: TX_HASH,
    from_address: FROM_ADDRESS,
    to_address: TO_ADDRESS,
    sla_result_id: 1,
    created_at: "2026-07-28T09:00:00.000Z",
    description: "SLA reward for rapid resolution",
  },
];

/**
 * Seed fixture for a failed payment awaiting retry (Issue #412 — the
 * payment retry-queue e2e journey). Callers of `mockApi` opt into seeding
 * one or more of these via the `failedPayments` option; the default is an
 * empty list so existing specs are unaffected.
 */
export interface FailedPaymentSeed {
  id: string;
  outage_id?: string;
  amount: number;
  asset_code: string;
  type: "reward" | "penalty";
  created_at: string;
}

const CORS_HEADERS = {
  "Access-Control-Allow-Credentials": "true",
  "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-CSRF-Token",
};

export interface MockApiOptions {
  /**
   * Failed payments to seed the retry queue with. Scoped to this call's
   * route handler closure (not module state) so tests never bleed into one
   * another regardless of execution order. Defaults to none.
   */
  failedPayments?: FailedPaymentSeed[];
}

export async function mockApi(
  page: Page,
  options: MockApiOptions = {},
): Promise<void> {
  // Mutable per-call fixture list — retrying a payment splices it out, so
  // subsequent GETs (e.g. after the retry-queue view refetches) reflect it.
  const failedPayments = (options.failedPayments ?? []).map((seed) => ({
    ...seed,
    status: "failed" as const,
    transaction_hash: "",
    from_address: FROM_ADDRESS,
    to_address: TO_ADDRESS,
  }));

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/\/$/, "");
    const method = request.method().toUpperCase();
    const origin = request.headers()["origin"] ?? "*";

    const cors = {
      ...CORS_HEADERS,
      "Access-Control-Allow-Origin": origin,
    };

    // Preflight requests must be acknowledged with the CORS allow-list.
    if (method === "OPTIONS") {
      return route.fulfill({ status: 204, headers: cors });
    }

    const json = (status: number, body: unknown) =>
      route.fulfill({
        status,
        contentType: "application/json",
        headers: cors,
        body: JSON.stringify(body),
      });

    /* ----------------------------- Auth ----------------------------- */
    if (method === "POST" && path === "/api/v1/auth/register") {
      const body = request.postDataJSON() as {
        email?: string;
        password?: string;
        full_name?: string;
      };
      const user: RegisteredUser = {
        id: `user-${registeredUsers.length + 2}`,
        email: body.email ?? "user@example.com",
        password: body.password ?? "",
        role: "engineer",
        ...(body.full_name !== undefined ? { full_name: body.full_name } : {}),
      };
      registeredUsers.push(user);
      return json(201, {
        id: user.id,
        email: user.email,
        full_name: user.full_name,
        role: user.role,
        created_at: new Date().toISOString(),
      });
    }

    if (method === "POST" && path === "/api/v1/auth/login") {
      const body = request.postDataJSON() as { email?: string; password?: string };
      const registered = registeredUsers.find(
        (u) => u.email === body.email && u.password === body.password
      );
      if (registered) {
        return json(200, {
          access_token: "mock-access-token",
          refresh_token: "mock-refresh-token",
          user: {
            id: registered.id,
            email: registered.email,
            role: registered.role,
            full_name: registered.full_name,
          },
        });
      }
      if (body.email === "ops@example.com" && body.password === "password123") {
        return json(200, {
          access_token: "mock-access-token",
          refresh_token: "mock-refresh-token",
          user: MOCK_USER,
        });
      }
      return json(401, { message: "Invalid credentials" });
    }

    // Cookie-only session check. The app prefers this endpoint during
    // bootstrap so a hard refresh can restore the session; the mock treats
    // any reachable session as valid (tests always log in first).
    if (method === "GET" && path === "/api/v1/auth/session") {
      return json(200, MOCK_USER);
    }

    if (method === "GET" && path === "/api/v1/auth/me") {
      return json(200, MOCK_USER);
    }

    if (method === "POST" && path === "/api/v1/auth/logout") {
      return json(200, { message: "Logged out" });
    }

    if (method === "POST" && path === "/api/v1/auth/refresh") {
      return json(200, {
        access_token: "mock-access-token",
        refresh_token: "mock-refresh-token",
      });
    }

    /* ---------------------------- Outages ---------------------------- */
    if (method === "GET" && path === "/api/v1/outages") {
      return json(200, { items: outages, total: outages.length });
    }

    if (method === "POST" && path === "/api/v1/outages") {
      const body = request.postDataJSON() as Partial<OutageRecord>;
      const created: OutageRecord = {
        id: `OUT-${Date.now()}`,
        site_name: body.site_name ?? "New Site",
        severity: (body.severity as OutageRecord["severity"]) ?? "medium",
        status: (body.status as OutageRecord["status"]) ?? "open",
        detected_at: body.detected_at ?? new Date().toISOString(),
        description: body.description ?? "",
        affected_services: body.affected_services ?? [],
        ...(body.affected_subscribers !== undefined
          ? { affected_subscribers: body.affected_subscribers }
          : {}),
        ...(body.assigned_to !== undefined ? { assigned_to: body.assigned_to } : {}),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        resolved_at: null,
        sla_status: null,
      };
      outages.push(created);
      return json(201, created);
    }

    /* ------------------------- Bulk import ---------------------------- */
    // Must be handled before the generic /outages/:id matcher below, since
    // "/outages/bulk" would otherwise be treated as a single-outage lookup.
    if (method === "POST" && path === "/api/v1/outages/bulk") {
      const raw = request.postData() ?? "";
      const filenameMatch = raw.match(/filename="([^"]+)"/);
      const filename = filenameMatch?.[1] ?? "upload.csv";

      // Fixtures named with "invalid" trigger a mocked server-side
      // validation failure (e.g. a business-rule check the client can't
      // perform), so the negative-case journey can be exercised without a
      // real backend.
      const isInvalidFixture = filename.toLowerCase().includes("invalid");

      const errors: BulkImportErrorRecord[] = isInvalidFixture
        ? [
            {
              row: 2,
              field: "start_time",
              message: "start_time must be before end_time.",
            },
          ]
        : [];

      const result = {
        imported: isInvalidFixture ? 1 : 2,
        skipped: isInvalidFixture ? 1 : 0,
        errors,
      };

      bulkImportHistory.unshift({
        id: `BULK-${bulkImportHistory.length + 1}`,
        filename,
        imported: result.imported,
        skipped: result.skipped,
        error_count: errors.length,
        errors,
        created_at: new Date().toISOString(),
      });

      return json(200, result);
    }

    if (method === "GET" && path === "/api/v1/outages/bulk/history") {
      return json(200, bulkImportHistory);
    }

    const resolveMatch = path.match(/^\/api\/v1\/outages\/([^/]+)\/resolve$/);
    if (resolveMatch && method === "POST") {
      const outage = outages.find((item) => item.id === resolveMatch[1]);
      if (!outage) return json(404, { message: "Not found" });

      const body = request.postDataJSON() as { mttr_minutes?: number };
      const mttrMinutes = body.mttr_minutes ?? 30;

      outage.status = "resolved";
      outage.resolved_at = new Date().toISOString();
      outage.resolution_notes = `Resolved in ${mttrMinutes} minutes`;
      outage.sla_status = {
        status: "met",
        mttr_minutes: mttrMinutes,
        threshold_minutes: 60,
        amount: 125,
        payment_type: "reward",
        rating: "excellent",
      };

      return json(200, {
        outage,
        payment: {
          id: `PAY-${Date.now()}`,
          outage_id: outage.id,
          amount: 125,
          asset_code: "USDC",
          type: "reward",
          status: "completed",
          transaction_hash: TX_HASH,
          from_address: FROM_ADDRESS,
          to_address: TO_ADDRESS,
          sla_result_id: 1,
          created_at: new Date().toISOString(),
          description: "SLA reward for rapid resolution",
        },
        sla: outage.sla_status,
      });
    }

    const outageMatch = path.match(/^\/api\/v1\/outages\/([^/]+)$/);
    if (outageMatch) {
      const outage = outages.find((item) => item.id === outageMatch[1]);
      if (!outage) return json(404, { message: "Not found" });

      if (method === "GET") {
        return json(200, outage);
      }

      if (method === "PUT") {
        const body = request.postDataJSON() as Partial<OutageRecord>;
        Object.assign(outage, body);
        return json(200, outage);
      }

      if (method === "DELETE") {
        const index = outages.findIndex((item) => item.id === outageMatch[1]);
        outages.splice(index, 1);
        return json(200, { message: "Outage deleted" });
      }
    }

    /* ---------------------------- Payments --------------------------- */
    if (method === "GET" && path === "/api/v1/payments") {
      const sortBy = url.searchParams.get("sort_by") || "created_at";
      const sortDir = url.searchParams.get("sort_dir") || "desc";
      const statusFilter = url.searchParams.get("status");

      let combined = [...payments, ...failedPayments];
      if (statusFilter) {
        combined = combined.filter((p) => p.status === statusFilter);
      }

      const sorted = combined.sort((a, b) => {
        let cmp = 0;
        if (sortBy === "created_at") {
          cmp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
        } else if (sortBy === "amount") {
          cmp = a.amount - b.amount;
        } else if (sortBy === "status") {
          cmp = a.status.localeCompare(b.status);
        }
        return sortDir === "asc" ? cmp : -cmp;
      });
      return json(200, { items: sorted, total: sorted.length, page: 1, page_size: 100 });
    }

    // Payment retry — used by the retry-queue view (Issue #412), both for
    // the per-item "Retry" button and the bulk-retry confirmation dialog.
    const retryMatch = path.match(/^\/api\/v1\/payments\/([^/]+)\/retry$/);
    if (retryMatch && method === "POST") {
      const id = retryMatch[1];
      const index = failedPayments.findIndex((p) => p.id === id);
      if (index === -1) return json(404, { message: "Not found" });

      const [retried] = failedPayments.splice(index, 1);
      const completed = { ...retried, status: "completed" };
      return json(200, completed);
    }

    /* ------------------------------ SLA ------------------------------ */
    if (method === "GET" && path === "/api/v1/sla/disputes") {
      return json(200, { items: [], total: 0, page: 1, page_size: 5 });
    }

    if (method === "POST" && path === "/api/v1/sla/preview") {
      return json(200, {
        outage_id: "OUT-001",
        status: "met",
        mttr_minutes: 25,
        threshold_minutes: 60,
        amount: 125,
        payment_type: "reward",
        rating: "excellent",
      });
    }

    if (method === "GET" && path === "/api/v1/sla/analytics/dashboard") {
      return json(200, {
        total_outages: 1,
        total_violations: 0,
        total_rewards: 125,
        total_penalties: 0,
        net_payout: 125,
      });
    }

    if (method === "GET" && path === "/api/v1/sla/analytics/trends") {
      return json(200, [
        { date: "2026-07-28", total_outages: 1, violations: 0, rewards: 125, penalties: 0 },
      ]);
    }

    /* --------------------------- Preferences ------------------------- */
    if (path === "/api/v1/user/preferences") {
      // Mark the onboarding tour as done so it does not auto-start and
      // navigate the browser away mid-test.
      return json(200, { onboardingTourDone: true });
    }

    if (method === "GET" && path === "/api/v1/health") {
      return json(200, { status: "ok" });
    }

    /* Fall through for unhandled routes (e.g. the /auth/events SSE stream). */
    return route.continue();
  });
}
