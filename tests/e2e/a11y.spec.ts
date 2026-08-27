import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mock-api";

/**
 * Axe-core accessibility scan for the core pages.
 *
 * Policy: fail-on-any serious/critical violation (WCAG 2 A/AA + best
 * practices), scoped to the "serious" and "critical" impact levels. Axe
 * findings below that threshold ("minor"/"moderate") are informational only
 * and do not fail the run — they're logged via `test.info().attach` for
 * visibility instead of gating CI, since low-impact findings are more prone
 * to false positives on third-party/dynamic markup and shouldn't block
 * merges on their own. There is no recorded baseline of pre-existing
 * violations: this suite fails on *any* serious/critical finding, not just
 * new ones, so the first run is the baseline — any failure it reports on
 * introduction must be triaged and either fixed or explicitly excluded
 * (with a comment explaining why) rather than silently accepted.
 *
 * Scans both the unauthenticated login page and, once logged in, the four
 * primary authenticated surfaces: dashboard, outages, payments, settings.
 */

const SERIOUS_IMPACTS = ["serious", "critical"];

async function expectNoSeriousViolations(page: Page, pageName: string) {
  const results = await new AxeBuilder({ page }).analyze();

  const serious = results.violations.filter(
    (violation) => violation.impact && SERIOUS_IMPACTS.includes(violation.impact),
  );

  const minor = results.violations.filter(
    (violation) => !violation.impact || !SERIOUS_IMPACTS.includes(violation.impact),
  );

  if (minor.length > 0) {
    await test.info().attach(`${pageName}-minor-violations`, {
      body: JSON.stringify(minor, null, 2),
      contentType: "application/json",
    });
  }

  if (serious.length > 0) {
    await test.info().attach(`${pageName}-serious-violations`, {
      body: JSON.stringify(serious, null, 2),
      contentType: "application/json",
    });
  }

  expect(
    serious,
    `${pageName} has ${serious.length} serious/critical a11y violation(s): ${serious
      .map((v) => v.id)
      .join(", ")}`,
  ).toEqual([]);
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("password123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/\s*$/);
}

test.describe("Accessibility scan — core pages", () => {
  test("login page (unauthenticated)", async ({ page }) => {
    await mockApi(page);
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

    await expectNoSeriousViolations(page, "login");
  });

  test("dashboard (authenticated)", async ({ page }) => {
    await mockApi(page);
    await login(page);
    await expect(
      page.getByRole("heading", { name: /SLA Analytics Dashboard/i }),
    ).toBeVisible();

    await expectNoSeriousViolations(page, "dashboard");
  });

  test("outages list (authenticated)", async ({ page }) => {
    await mockApi(page);
    await login(page);
    await page.goto("/outages");
    // Wait for the outages toolbar to render before scanning. Note: the
    // outages list table itself does not currently render seeded/mocked
    // records (OutagesPageClient defaults its `data` prop to `[]` and the
    // page never passes one in, so `src/features/outages/hooks/useOutages`
    // is unused) — that's a pre-existing data-wiring gap outside the scope
    // of this a11y suite, so this scan targets the page as it actually
    // renders today (toolbar + empty table state) rather than asserting on
    // row data that never appears.
    await expect(page.getByPlaceholder("Search outages...")).toBeVisible({
      timeout: 20_000,
    });

    await expectNoSeriousViolations(page, "outages");
  });

  test("payments (authenticated)", async ({ page }) => {
    await mockApi(page);
    await login(page);
    await page.goto("/payments");
    await expect(
      page.getByRole("heading", { level: 1, name: "Payments", exact: true }),
    ).toBeVisible();

    await expectNoSeriousViolations(page, "payments");
  });

  test("settings (authenticated)", async ({ page }) => {
    await mockApi(page);
    await login(page);
    await page.goto("/setting");
    // Settings heading is i18n-driven; wait for the wallet card content
    // rather than a fixed heading string.
    await expect(page.locator("h1")).toBeVisible();

    await expectNoSeriousViolations(page, "settings");
  });
});
