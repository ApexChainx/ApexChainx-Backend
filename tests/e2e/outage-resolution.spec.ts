import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test.describe("Outage resolution flow", () => {
  test("creates an outage, resolves it with MTTR, and shows the SLA result and payment transaction", async ({
    page,
  }) => {
    await mockApi(page);

    // Create a new outage via the UI.
    await page.goto("/outages/new");
    await expect(
      page.getByRole("heading", { name: /Create Outage/i }),
    ).toBeVisible();

    await page.getByPlaceholder("e.g. Lagos Node 1").fill("Nairobi Edge 7");
    await page
      .getByPlaceholder("Describe the outage…")
      .fill("Traffic spikes caused an elevated error rate.");
    await page.getByRole("button", { name: /Create Outage/i }).click();

    // Land on the outage detail page.
    await expect(
      page.getByRole("heading", { level: 1, name: /^Outage /i }),
    ).toBeVisible();
    await expect(page.getByText("Nairobi Edge 7")).toBeVisible();

    // Open the resolve modal and enter the MTTR.
    await page.getByRole("button", { name: /Resolve Outage/i }).click();
    await page.getByLabel(/Mean time to resolve/i).fill("25");
    await page.getByRole("button", { name: /Confirm resolution/i }).click();

    // SLA result should be rendered once resolution completes.
    await expect(page.getByRole("heading", { name: /SLA Result/i })).toBeVisible();
    await expect(page.getByText("excellent", { exact: true })).toBeVisible();

    // The generated resolution payment must show a transaction hash as a link.
    await expect(
      page.getByRole("heading", { name: /Resolution Payment/i }),
    ).toBeVisible();
    const txLink = page.getByRole("link", { name: /0a1b2c3d/ });
    await expect(txLink).toBeVisible();
    await expect(txLink).toHaveAttribute(
      "href",
      /stellar\.expert\/explorer\/testnet\/tx\/0a1b2c3d/,
    );
  });
});
