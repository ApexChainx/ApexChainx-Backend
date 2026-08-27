import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mock-api";

/**
 * Bulk import: upload -> result -> history journey.
 *
 * Uses in-memory CSV fixtures (no filesystem fixture files) and the
 * route-based backend mock in `mock-api.ts`, which keeps its bulk-import
 * history in a module-scoped array. Each test uses a unique, timestamped
 * filename so assertions can target "this test's record" via its filename
 * rather than assuming the history list is empty, since the mock's history
 * state is not reset between tests in the same worker.
 */

const VALID_HEADERS = "service_id,start_time,end_time";

function validCsv(): string {
  return [VALID_HEADERS, "s1,2026-01-01T00:00:00Z,2026-01-02T00:00:00Z"].join("\n");
}

// Passes the client-side required-column/required-field checks (so the
// Upload button is enabled), but encodes a business-rule violation
// (start_time after end_time) that only the mocked "backend" rejects —
// exercising the server-validation-errors path rather than the client's
// blocking-error path.
function invalidCsv(): string {
  return [VALID_HEADERS, "s1,2026-01-02T00:00:00Z,2026-01-01T00:00:00Z"].join("\n");
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("password123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/\s*$/);
}

test.describe("Bulk import journey", () => {
  test("uploads a valid CSV, shows the result, and lists it in history", async ({ page }) => {
    await mockApi(page);
    await login(page);

    const filename = `valid-import-${Date.now()}.csv`;

    await page.goto("/bulk-import");
    await expect(page.getByRole("heading", { name: "Bulk Outage Import" })).toBeVisible();

    await page.getByLabel("Choose file").setInputFiles({
      name: filename,
      mimeType: "text/csv",
      buffer: Buffer.from(validCsv()),
    });

    // File accepted and previewed before upload.
    await expect(page.getByText(filename)).toBeVisible();

    const uploadButton = page.getByRole("button", { name: /upload file/i });
    await expect(uploadButton).toBeEnabled();
    await uploadButton.click();

    // Result view renders a success summary.
    await expect(page.getByText("Import Summary")).toBeVisible();
    await expect(page.getByText("Imported")).toBeVisible();

    // History shows the new record.
    await page.getByRole("link", { name: /view history/i }).click();
    await expect(page.getByRole("heading", { name: "Import History" })).toBeVisible();
    await expect(page.getByText(filename)).toBeVisible();
    await expect(page.getByText(/2 imported/i)).toBeVisible();
  });

  test("uploads a CSV the backend rejects and shows the per-row error, then lists it in history", async ({
    page,
  }) => {
    await mockApi(page);
    await login(page);

    const filename = `invalid-import-${Date.now()}.csv`;

    await page.goto("/bulk-import");

    await page.getByLabel("Choose file").setInputFiles({
      name: filename,
      mimeType: "text/csv",
      buffer: Buffer.from(invalidCsv()),
    });

    await expect(page.getByText(filename)).toBeVisible();

    // The row is well-formed (all required columns present and non-empty),
    // so client-side validation passes and the upload proceeds — the
    // rejection comes back from the (mocked) server.
    const uploadButton = page.getByRole("button", { name: /upload file/i });
    await expect(uploadButton).toBeEnabled();
    await uploadButton.click();

    await expect(page.getByText("Import Summary")).toBeVisible();
    await expect(page.getByText(/1 validation error/i)).toBeVisible();
    await expect(page.getByText(/start_time must be before end_time/i)).toBeVisible();

    // Error report download affordance renders for the failed rows.
    await expect(page.getByRole("button", { name: /download report/i })).toBeVisible();

    // History reflects the error count for this import.
    await page.getByRole("link", { name: /view history/i }).click();
    await expect(page.getByText(filename)).toBeVisible();
    await expect(page.getByText(/1 errors/i)).toBeVisible();
  });
});
