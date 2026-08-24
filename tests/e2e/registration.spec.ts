import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test.describe("Registration flow", () => {
  test("signs up, gets auto-logged-in, and lands on the dashboard", async ({
    page,
  }) => {
    await mockApi(page);

    // Sign up with a fresh account.
    await page.goto("/register");
    await page.getByLabel("Email").fill("new-operator@example.com");
    await page.getByLabel("Password", { exact: true }).fill("password123");
    await page.getByLabel("Confirm password").fill("password123");
    await page.getByRole("button", { name: /Create account/i }).click();

    // Auto-login after registration should land on the dashboard.
    await expect(page).toHaveURL(/\/\s*$/);
    await expect(
      page.getByRole("heading", { name: /SLA Analytics Dashboard/i }),
    ).toBeVisible();
  });
});
