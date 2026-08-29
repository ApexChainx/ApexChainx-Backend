import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mock-api";

/**
 * Offline-first outage viewing (#401).
 *
 * `useOutages` persists every successful /outages response to IndexedDB and
 * hydrates from that store on mount. This spec proves the browser flow:
 * load the list online (populating IndexedDB), then reload with every
 * /outages request cut off at the network layer, and assert the cached list
 * still renders even though no /outages request can succeed during the
 * offline phase.
 *
 * Mechanics:
 * - `mockApi` still fulfills non-outage API traffic (e.g. the cookie-only
 *   /auth/session bootstrap).
 * - Every /outages request after the reload is explicitly aborted so the
 *   rendered list can only come from the IndexedDB hydration.
 */

const CACHE_DB = "apexchain-cache";

async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("password123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/\s*$/);
}

async function wipeIndexedDb(page: Page): Promise<void> {
  await page.evaluate(
    (dbName) =>
      new Promise<void>((resolve) => {
        const request = indexedDB.deleteDatabase(dbName);
        request.onsuccess = () => resolve();
        request.onerror = () => resolve();
        request.onblocked = () => resolve();
      }),
    CACHE_DB
  );
}

test("renders the cached outage list after an offline reload", async ({
  page,
}) => {
  await mockApi(page);

  await login(page);

  // Wipe any cached outages from a previous run so this test starts cold.
  // Do this after login so the page is on the app origin (IndexedDB is
  // denied on about:blank).
  await wipeIndexedDb(page);

  // Load the outages page online so the fetch populates IndexedDB.
  await page.goto("/outages");
  await expect(page.getByPlaceholder("Search outages...")).toBeVisible();
  await expect(page.getByText("Lagos Node 1")).toBeVisible();

  // Give the fire-and-forget IndexedDB write time to land before the network
  // route is cut.
  await page.waitForTimeout(750);

  // Offline phase: every /outages request is aborted at the network layer, so
  // any outage rendered after the reload must come from the IndexedDB
  // hydration rather than a successful network response. If the app ever fired
  // a /outages request it would be aborted here.
  await page.route("**/api/v1/outages", (route) =>
    route.abort("internetdisconnected")
  );

  await page.reload();

  // The cached outage still renders, straight from IndexedDB — hydration
  // serves the list without a successful network round-trip.
  await expect(page.getByPlaceholder("Search outages...")).toBeVisible();
  await expect(page.getByText("Lagos Node 1")).toBeVisible();

  // Clean the cache so subsequent runs start cold.
  await wipeIndexedDb(page);
});
