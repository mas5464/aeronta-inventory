import { expect, test } from "@playwright/test";

/**
 * Slice S8 hardening — best-effort e2e (per the slice scope, item 5): load
 * the Workbench against a mocked BFF (route-intercepted `/v1/**`, no real
 * backend/Docker needed) → accept a recommendation → the row leaves the
 * list. This is the one scenario the slice asked for; further e2e coverage
 * is future work (see apps/web/UAT.md).
 */

const PENDING_ROW = {
  recommendation_id: "rec-e2e-1",
  pn: "HYD-PUMP-001",
  location: "YYZ",
  type: "purchase",
  criticality_tier: 1,
  aog_risk_level: 3,
  confidence_score: 0.92,
  recommended_quantity: 4,
  estimated_cost_impact: -8400,
  tier: 1,
  priority_score: 45.9,
  status: "pending",
  reason: "Projected shortage within lead time",
  approvable: true,
  description: "Hydraulic pump",
  current_stock: 4,
  shortage_quantity: 3,
  recommended_location: null,
  horizon_days: 90,
};

test("accepting a recommendation in the Workbench removes it from the list", async ({ page }) => {
  let approved = false;

  await page.route("**/v1/tenants/acme/recommendations?**", async (route) => {
    const items = approved ? [] : [PENDING_ROW];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items, total: items.length, limit: 25, offset: 0 }),
    });
  });

  await page.route("**/v1/tenants/acme/killswitch", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ engaged: false }),
    });
  });

  await page.route("**/v1/tenants/acme/recommendations/rec-e2e-1/approve", async (route) => {
    approved = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        recommendation_id: "rec-e2e-1",
        status: "approved",
        writeback: null,
        message: "",
      }),
    });
  });

  await page.goto("/#/workbench");

  await expect(page.getByText("HYD-PUMP-001")).toBeVisible();

  await page.getByRole("button", { name: "Accept", exact: true }).click();

  await expect(page.getByText("HYD-PUMP-001")).not.toBeVisible();
  await expect(page.getByText("No recommendations match the current filters.")).toBeVisible();
});
