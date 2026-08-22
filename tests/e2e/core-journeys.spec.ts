import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test.describe("Core user journeys", () => {
  test("logs in, creates an outage, resolves it, and views payments", async ({ page }) => {
    await mockApi(page);

    // Log in.
    await page.goto("/login");
    await page.getByLabel("Email").fill("ops@example.com");
    await page.getByLabel("Password").fill("password123");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/\s*$/);
    await expect(
      page.getByRole("heading", { name: /SLA Analytics Dashboard/i }),
    ).toBeVisible();

    // Create a new outage.
    await page.goto("/outages/new");
    await expect(
      page.getByRole("heading", { name: /Create Outage/i }),
    ).toBeVisible();
    await page.getByPlaceholder("e.g. Lagos Node 1").fill("Nairobi Edge 7");
    await page
      .getByPlaceholder("Describe the outage…")
      .fill("Traffic spikes caused an elevated error rate.");
    await page.getByRole("button", { name: /Create Outage/i }).click();

    // Resolve it.
    await expect(
      page.getByRole("heading", { level: 1, name: /^Outage /i }),
    ).toBeVisible();
    await page.getByRole("button", { name: /Resolve Outage/i }).click();
    await page.getByLabel(/Mean time to resolve/i).fill("25");
    await page.getByRole("button", { name: /Confirm resolution/i }).click();
    await expect(page.getByText(/Resolution Payment/i)).toBeVisible();
    await expect(page.getByText(/USDC/i)).toBeVisible();

    // View payments.
    await page.getByRole("link", { name: /Payments/i }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Payments", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("USDC")).toBeVisible();
  });
});
