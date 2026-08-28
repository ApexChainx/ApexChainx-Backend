import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

/**
 * Issue #412 — Payment retry-queue journey.
 *
 * Covers src/components/payments/retry-queue-view.tsx: the failed-payment
 * table renders against a backend seeded with a failed payment, a row can
 * be selected and bulk-retried through the confirmation dialog (and leaves
 * the list once retried), a single row can be retried directly via its
 * per-item "Retry" button, and the empty state renders when there are no
 * failed payments in the window.
 */

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("password123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/\s*$/);
}

test.describe("Payment retry queue", () => {
  test("renders the failed-payment table and completes a bulk retry via the confirmation dialog", async ({
    page,
  }) => {
    await mockApi(page, {
      failedPayments: [
        {
          id: "PAY-FAIL-1",
          outage_id: "OUT-001",
          amount: 250,
          asset_code: "USDC",
          type: "penalty",
          created_at: "2026-08-25T09:00:00.000Z",
        },
        {
          id: "PAY-FAIL-2",
          outage_id: "OUT-002",
          amount: 75,
          asset_code: "USDC",
          type: "reward",
          created_at: "2026-08-26T09:00:00.000Z",
        },
      ],
    });

    await login(page);

    await page.goto("/payments/retry-queue");
    await expect(
      page.getByRole("heading", { name: "Payment Retry Queue" }),
    ).toBeVisible();

    // Both seeded failed payments render as rows in the table.
    const rowOne = page.getByRole("row", { name: /OUT-001/ });
    const rowTwo = page.getByRole("row", { name: /OUT-002/ });
    await expect(rowOne).toBeVisible();
    await expect(rowTwo).toBeVisible();

    // Select the first row and trigger the bulk-retry confirmation dialog.
    await rowOne.getByRole("checkbox").check();
    await page.getByRole("button", { name: /Bulk Retry \(1\)/ }).click();

    await expect(
      page.getByRole("heading", { name: "Confirm Bulk Retry" }),
    ).toBeVisible();
    await expect(
      page.getByText(/Are you sure you want to retry 1 payment/),
    ).toBeVisible();

    await page.getByRole("button", { name: "Confirm Retry" }).click();

    // The dialog closes and the retried row leaves the list; the
    // untouched row remains.
    await expect(
      page.getByRole("heading", { name: "Confirm Bulk Retry" }),
    ).not.toBeVisible();
    await expect(rowOne).not.toBeVisible();
    await expect(rowTwo).toBeVisible();
  });

  test("retries a single payment via the per-item Retry button", async ({
    page,
  }) => {
    await mockApi(page, {
      failedPayments: [
        {
          id: "PAY-FAIL-3",
          outage_id: "OUT-003",
          amount: 40,
          asset_code: "USDC",
          type: "penalty",
          created_at: "2026-08-27T09:00:00.000Z",
        },
      ],
    });

    await login(page);
    await page.goto("/payments/retry-queue");

    const row = page.getByRole("row", { name: /OUT-003/ });
    await expect(row).toBeVisible();

    // Per-item retry does not go through the confirmation dialog.
    await row.getByRole("button", { name: /Retry/ }).click();

    await expect(row).not.toBeVisible();
    // With no failed payments left, the empty state takes over.
    await expect(page.getByText("No failed payments")).toBeVisible();
  });

  test("shows the empty state when there are no failed payments in the window", async ({
    page,
  }) => {
    await mockApi(page); // no failedPayments seeded

    await login(page);
    await page.goto("/payments/retry-queue");

    await expect(
      page.getByRole("heading", { name: "Payment Retry Queue" }),
    ).toBeVisible();
    await expect(page.getByText("No failed payments")).toBeVisible();
    await expect(
      page.getByText("There are no failed payments from the last 7 days."),
    ).toBeVisible();
  });
});
