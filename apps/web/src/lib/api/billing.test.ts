import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * `state.supabase` is `vi.hoisted` so the `vi.mock` factory below (itself
 * hoisted above this `const` by Vitest) can close over it — same pattern as
 * `TenantSwitcher.test.tsx`. Each test mutates `state.supabase` before
 * importing `./billing` so `getPublicPrices` sees the right client shape
 * (including `null`, the auth-disabled-dev default).
 */
const state = vi.hoisted(() => ({
  supabase: null as unknown,
}));

vi.mock("@/lib/auth/supabase", () => ({
  get supabase() {
    return state.supabase;
  },
}));

import { getBilling, createCheckoutSession, getPublicPrices } from "./billing";

describe("billing api", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    state.supabase = null;
  });

  it("getBilling calls the BFF billing route and returns the summary", async () => {
    const summary = {
      plan_tier: "growth",
      subscription_status: "active",
      key_quota: 25000,
      keys_used: 42,
      current_period_end: null,
      trial_ends_at: null,
    };
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(summary), { status: 200 }));
    const out = await getBilling("aeronta-demo");
    expect(out.keys_used).toBe(42);
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("/v1/tenants/aeronta-demo/billing");
  });

  it("createCheckoutSession posts price_id to the function and returns the url", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ url: "https://stripe/checkout" }), { status: 200 }));
    const url = await createCheckoutSession("aeronta-demo", "price_growth");
    expect(url).toBe("https://stripe/checkout");
    const call = fetchSpy.mock.calls[0];
    expect(call[0]).toContain("/functions/v1/create-checkout-session");
    const body = call[1]?.body as string;
    expect(JSON.parse(body).price_id).toBe("price_growth");
  });

  it("getPublicPrices returns [] when supabase is null (auth-disabled dev)", async () => {
    state.supabase = null;
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const out = await getPublicPrices();
    expect(out).toEqual([]);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
