import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mock-api";

/**
 * Offline-first outage viewing (#401).
 *
 * `useOutages` persists every successful /outages response to IndexedDB and
 * hydrates from that store on mount. This spec proves the real browser flow:
 * load the list online (populating IndexedDB), take the network away, reload,
 * and assert the cached list still renders even though no /outages request
 * can succeed during the offline phase.
 *
 * Mechanics:
 * - The service worker (precached on the online visit) serves the app shell
 *   for the offline document reload.
 * - `mockApi` still fulfills non-outage API traffic (e.g. the cookie-only
 *   /auth/session bootstrap) — route handlers run before network emulation.
 * - Every /outages request during the offline phase is explicitly aborted so
 *   the rendered list can only come from the IndexedDB hydration.
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
  await wipeIndexedDb(page);

  await login(page);

  // Load the outages page online so the fetch populates IndexedDB.
  await page.goto("/outages");
  await expect(page.getByPlaceholder("Search outages...")).toBeVisible();
  await expect(page.getByText("Lagos Node 1")).toBeVisible();

  // Make sure the service worker is installed and controlling the page so the
  // offline document reload can be served from its cache.
  await page.waitForFunction(async () => {
    if (!("serviceWorker" in navigator)) return true;
    const registration = await navigator.serviceWorker.ready;
    return Boolean(registration.active) && navigator.serviceWorker.controller !== null;
  });

  // Give the fire-and-forget IndexedDB write time to land before the network
  // disappears.
  await page.waitForTimeout(750);

  // Offline phase: the network goes away and every /outages request is
  // aborted, so any outage rendered after the reload must come from the
  // IndexedDB hydration rather than a successful network response.
  let offlineOutageRequests = 0;
  await page.route("**/api/v1/outages", async (route) => {
    offlineOutageRequests += 1;
    await route.abort("internetdisconnected");
  });
  await page.context().setOffline(true);

  await page.reload();

  // The app did try to refetch the list — and every attempt was cut off.
  await expect.poll(() => offlineOutageRequests).toBeGreaterThan(0);

  // Yet the cached outage still renders, straight from IndexedDB.
  await expect(page.getByPlaceholder("Search outages...")).toBeVisible();
  await expect(page.getByText("Lagos Node 1")).toBeVisible();

  // Restore the network and clean the cache so subsequent runs start cold.
  await page.context().setOffline(false);
  await wipeIndexedDb(page);
});