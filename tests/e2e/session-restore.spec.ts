import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mock-api";

/**
 * Session restore on hard refresh (#400).
 *
 * The session-restore flow (`GET /auth/session` cookie-only bootstrap, the
 * `noc_session_seen` localStorage flag) is unit-tested in isolation. These
 * specs prove the real browser behavior: a hard refresh keeps the user signed
 * in, that restore uses the cookie-only endpoint with no Authorization header,
 * and that clearing cookies plus the localStorage flag forces a redirect back
 * to /login.
 */

async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("password123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/\s*$/);
  await expect(
    page.getByRole("heading", { name: /SLA Analytics Dashboard/i }),
  ).toBeVisible();
}

test("keeps the session across a hard refresh and restores via /auth/session", async ({
  page,
}) => {
  await mockApi(page);

  // Capture the Authorization header of every /auth/session call so we can
  // assert the restore is cookie-only (no bearer token sent).
  let sessionAuthorization: string | null = null;
  let sessionCallCount = 0;
  await page.route("**/api/v1/auth/session", async (route) => {
    sessionCallCount += 1;
    sessionAuthorization =
      route.request().headers()["authorization"] ?? null;
    await route.fallback();
  });

  await login(page);

  // Hard refresh mid-session: in-memory tokens are gone, but the httpOnly
  // session cookie + noc_session_seen flag survive, so the app must restore.
  // Reset the counters so we only measure the reload-time bootstrap.
  sessionCallCount = 0;
  sessionAuthorization = null;
  await page.reload();

  // The dashboard renders the authenticated user — not the login redirect,
  // and not a persistent loading state.
  await expect(
    page.getByRole("heading", { name: /SLA Analytics Dashboard/i }),
  ).toBeVisible();

  // And that restore went through the cookie-only contract: /auth/session
  // fired (at least once) with no Authorization header.
  expect(sessionCallCount).toBeGreaterThan(0);
  expect(sessionAuthorization).toBeNull();
});

test("cleared cookies and storage flag redirect a hard refresh to /login", async ({
  page,
}) => {
  await mockApi(page);
  await login(page);

  // Simulate a wiped browser: drop the `noc_session_seen` flag from
  // localStorage and remove every cookie (including the session cookies).
  await page.evaluate(() => localStorage.clear());
  await page.context().clearCookies();

  await page.reload();

  // Without any session evidence the bootstrap short-circuits to
  // unauthenticated and RouteGuard redirects to the login page.
  await expect(page).toHaveURL(/\/login/);
  await expect(
    page.getByRole("button", { name: "Sign in" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /SLA Analytics Dashboard/i }),
  ).toHaveCount(0);
});