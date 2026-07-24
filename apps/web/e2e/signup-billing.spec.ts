import { expect, test, type Page } from "@playwright/test";

/**
 * C4 Task 16 — best-effort e2e (route-mocked, no backend/Docker), same
 * posture as `workbench-accept.spec.ts`: mocks the BFF's
 * `GET /v1/tenants/{tenant}/billing` route and drives the frontend through
 * the three billing states `BillingPage` renders off `subscription_status`
 * (see `BillingPage.tsx`'s `planStateOf`). The full
 * signup → Stripe test checkout → webhook → `tenants.plan_tier` chain
 * needs the deployed Edge Functions + Stripe test mode and is verified via
 * the rollout runbook (`deploy/C4_ROLLOUT.md`), not this harness.
 *
 * IMPORTANT — the e2e dev server (`playwright.config.ts`'s `webServer`)
 * runs plain `npm run dev` with no `VITE_SUPABASE_*` env, i.e. auth
 * DISABLED: `useAuth().role` is always `null` (there is no session to
 * decode JWT claims from). `BillingPage` gates its owner-only actions
 * ("Start subscription" / "Manage billing" buttons) behind
 * `role === "owner"` — those buttons are therefore UNREACHABLE in this
 * harness, and every state instead falls through to the role-independent
 * "Ask an owner to manage billing." copy (verified below, once per state,
 * as proof the page rendered past loading rather than erroring). This spec
 * instead asserts the role-INDEPENDENT signals that faithfully distinguish
 * the three states: the plan card's "Status: …" line (rendered only once
 * `subscription_status` is truthy — i.e. absent pre-subscription), the
 * read-only alert (rendered unconditionally for canceled/unpaid/paused,
 * NOT owner-gated), and the usage meter (rendered unconditionally,
 * regardless of state). Real owner-CTA visibility is a live-auth concern,
 * covered by the rollout runbook's manual test-checkout step, not by this
 * mocked harness.
 *
 * Also: the "Billing" nav item is itself role-gated
 * (`role === "owner"`, `App.tsx`'s `BILLING_NAV_ITEM`) and so is invisible
 * here too — the `/billing` route is registered unconditionally regardless
 * of nav visibility, so this spec navigates straight to the hash URL
 * rather than clicking a nav link.
 */

const BASE_SUMMARY = {
  plan_tier: "growth",
  key_quota: 25000,
  keys_used: 5000,
  current_period_end: null,
  trial_ends_at: null,
};

async function mockBilling(page: Page, overrides: Record<string, unknown>) {
  await page.route("**/v1/tenants/acme/billing", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...BASE_SUMMARY, ...overrides }),
    });
  });
}

test("provisioning billing summary (no subscription yet)", async ({ page }) => {
  await mockBilling(page, { subscription_status: null, keys_used: 0 });
  await page.goto("/#/billing");

  await expect(page.getByRole("heading", { name: "Plan" })).toBeVisible();
  await expect(page.getByText(/^Status:/)).not.toBeVisible();
  await expect(page.getByRole("alert")).not.toBeVisible();
  await expect(page.getByText("Ask an owner to manage billing.")).toBeVisible();
});

test("canceled billing summary shows the read-only/reactivate notice", async ({ page }) => {
  await mockBilling(page, { subscription_status: "canceled" });
  await page.goto("/#/billing");

  await expect(page.getByText("Status: canceled")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(/read-only/i);
  await expect(page.getByRole("alert")).toContainText(/reactivate/i);
  await expect(page.getByText("Ask an owner to manage billing.")).toBeVisible();
});

test("active billing summary shows plan status and the usage meter", async ({ page }) => {
  await mockBilling(page, { subscription_status: "active", keys_used: 12500, key_quota: 25000 });
  await page.goto("/#/billing");

  await expect(page.getByText("Status: active")).toBeVisible();
  await expect(page.getByRole("alert")).not.toBeVisible();
  const meter = page.getByRole("progressbar");
  await expect(meter).toBeVisible();
  await expect(meter).toHaveAttribute("aria-valuenow", "50");
  await expect(page.getByText("12,500 / 25,000")).toBeVisible();
  await expect(page.getByText("Ask an owner to manage billing.")).toBeVisible();
});
