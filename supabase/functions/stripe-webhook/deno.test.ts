import { assertEquals, assertRejects } from "jsr:@std/assert@1";
import { applyEvent } from "./sync.ts";

// A fake admin client recording upserts + tenant updates, with a plan_tiers
// lookup. Every upsert/update/select chain returns an `{error}` (or
// `{data, error}`) shape — sync.ts checks these and throws on failure, so the
// fakes here must be able to simulate both the happy path and specific
// per-table errors (see the "propagates" test below).
function fakeAdmin(opts: {
  planQuota?: Record<string, number>;
  tenantsUpdateError?: { message: string } | null;
  // stripe_customer_id -> tenant id, backing the sync.ts customer-fallback
  // lookup (`tenants.select("id").eq("stripe_customer_id", ...)`).
  customerLookup?: Record<string, string>;
} = {}) {
  const planQuota = opts.planQuota ??
    { growth: 25000, scale: 100000, starter: 5000 };
  const tenantsUpdateError = opts.tenantsUpdateError ?? null;
  const customerLookup = opts.customerLookup ?? {};
  // deno-lint-ignore no-explicit-any
  const calls: any[] = [];
  return {
    calls,
    // deno-lint-ignore no-explicit-any
    from(table: string): any {
      return {
        // deno-lint-ignore no-explicit-any
        upsert: (row: any) => {
          calls.push({ table, op: "upsert", row });
          return { error: null };
        },
        // deno-lint-ignore no-explicit-any
        update: (row: any) => ({
          eq: (_c: string, v: string) => {
            calls.push({ table, op: "update", row, id: v });
            const error = table === "tenants" ? tenantsUpdateError : null;
            return { error };
          },
        }),
        select: () => ({
          eq: (col: string, v: string) => ({
            maybeSingle: () => {
              if (table === "plan_tiers") {
                const key_quota = planQuota[v];
                return {
                  data: key_quota === undefined ? null : { key_quota },
                  error: null,
                };
              }
              if (table === "tenants" && col === "stripe_customer_id") {
                const id = customerLookup[v];
                return { data: id ? { id } : null, error: null };
              }
              return { data: null, error: null };
            },
          }),
        }),
      };
    },
  };
}

Deno.test("subscription.updated syncs tenants plan_tier + key_quota from price.metadata.tier", async () => {
  const admin = fakeAdmin();
  await applyEvent(admin, {
    type: "customer.subscription.updated",
    data: {
      object: {
        id: "sub_1",
        status: "active",
        metadata: { tenant_id: "T1" },
        items: {
          data: [{ price: { id: "price_g", metadata: { tier: "growth" } } }],
        },
        current_period_end: 1893456000,
        trial_end: null,
        cancel_at_period_end: false,
      },
    },
  });
  const tenantUpdate = admin.calls.find((c) =>
    c.table === "tenants" && c.op === "update"
  );
  assertEquals(tenantUpdate.row.plan_tier, "growth");
  assertEquals(tenantUpdate.row.key_quota, 25000);
  assertEquals(tenantUpdate.row.subscription_status, "active");
});

Deno.test("subscription event with an unknown tier still syncs status fields but omits plan_tier/key_quota", async () => {
  const admin = fakeAdmin();
  await applyEvent(admin, {
    type: "customer.subscription.updated",
    data: {
      object: {
        id: "sub_2",
        status: "active",
        metadata: { tenant_id: "T1" },
        items: {
          data: [{
            price: { id: "price_x", metadata: { tier: "unobtainium" } },
          }],
        },
        current_period_end: 1893456000,
        trial_end: null,
        cancel_at_period_end: false,
      },
    },
  });
  const tenantUpdate = admin.calls.find((c) =>
    c.table === "tenants" && c.op === "update"
  );
  // Status fields still sync...
  assertEquals(tenantUpdate.row.subscription_status, "active");
  // ...but an unresolved tier must not appear in the patch at all (not even
  // as `undefined`/`null` — the key itself must be absent) since a missing
  // plan_tiers row is not the same thing as a resolved tier.
  assertEquals("plan_tier" in tenantUpdate.row, false);
  assertEquals("key_quota" in tenantUpdate.row, false);
});

Deno.test("a tenants-update error propagates out of applyEvent (so the handler can 500 and Stripe retries)", async () => {
  const admin = fakeAdmin({
    tenantsUpdateError: { message: "db unavailable" },
  });
  await assertRejects(() =>
    applyEvent(admin, {
      type: "customer.subscription.updated",
      data: {
        object: {
          id: "sub_3",
          status: "active",
          metadata: { tenant_id: "T1" },
          items: {
            data: [{ price: { id: "price_g", metadata: { tier: "growth" } } }],
          },
          current_period_end: 1893456000,
          trial_end: null,
          cancel_at_period_end: false,
        },
      },
    })
  );
});

Deno.test("subscription event with no metadata.tenant_id resolves the tenant via stripe_customer_id", async () => {
  const admin = fakeAdmin({ customerLookup: { cus_42: "T9" } });
  await applyEvent(admin, {
    type: "customer.subscription.created",
    data: {
      object: {
        id: "sub_9",
        status: "trialing",
        customer: "cus_42",
        // Stripe never copies Checkout Session metadata onto the
        // Subscription it creates -- this is the shape a real
        // `customer.subscription.created` event has.
        metadata: {},
        items: { data: [{ price: { id: "price_g", metadata: {} } }] },
        current_period_end: 1893456000,
        trial_end: null,
        cancel_at_period_end: false,
      },
    },
  });
  const subUpsert = admin.calls.find((c) =>
    c.table === "subscriptions" && c.op === "upsert"
  );
  assertEquals(subUpsert.row.tenant_id, "T9");
  const tenantUpdate = admin.calls.find((c) =>
    c.table === "tenants" && c.op === "update"
  );
  assertEquals(tenantUpdate.id, "T9");
});

Deno.test("subscription event with no metadata.tenant_id and no matching customer is un-attributable: no writes, no throw", async () => {
  const admin = fakeAdmin({ customerLookup: {} });
  await applyEvent(admin, {
    type: "customer.subscription.created",
    data: {
      object: {
        id: "sub_10",
        status: "trialing",
        customer: "cus_unknown",
        metadata: {},
        items: { data: [{ price: { id: "price_g", metadata: {} } }] },
        current_period_end: 1893456000,
        trial_end: null,
        cancel_at_period_end: false,
      },
    },
  });
  assertEquals(admin.calls.length, 0);
});

Deno.test("product.created upserts a products row with the expected shape", async () => {
  const admin = fakeAdmin();
  await applyEvent(admin, {
    type: "product.created",
    data: {
      object: {
        id: "prod_1",
        active: true,
        name: "Growth",
        description: "Growth plan",
        metadata: { tier: "growth" },
      },
    },
  });
  const call = admin.calls.find((c) =>
    c.table === "products" && c.op === "upsert"
  );
  assertEquals(call.row, {
    id: "prod_1",
    active: true,
    name: "Growth",
    description: "Growth plan",
    metadata: { tier: "growth" },
  });
});

Deno.test("product.deleted mirrors active:false even though Stripe's payload omits the field", async () => {
  const admin = fakeAdmin();
  await applyEvent(admin, {
    type: "product.deleted",
    data: {
      object: {
        id: "prod_1",
        // No `active` key at all -- mirrors Stripe's real deleted payload.
        name: "Growth",
      },
    },
  });
  const call = admin.calls.find((c) =>
    c.table === "products" && c.op === "upsert"
  );
  assertEquals(call.row.active, false);
});

Deno.test("price.created upserts a prices row with the expected shape", async () => {
  const admin = fakeAdmin();
  await applyEvent(admin, {
    type: "price.created",
    data: {
      object: {
        id: "price_1",
        product: "prod_1",
        active: true,
        unit_amount: 4900,
        currency: "usd",
        recurring: {
          interval: "month",
          interval_count: 1,
          trial_period_days: 14,
        },
        metadata: { tier: "growth" },
      },
    },
  });
  const call = admin.calls.find((c) =>
    c.table === "prices" && c.op === "upsert"
  );
  assertEquals(call.row, {
    id: "price_1",
    product_id: "prod_1",
    active: true,
    unit_amount: 4900,
    currency: "usd",
    interval: "month",
    interval_count: 1,
    trial_period_days: 14,
    metadata: { tier: "growth" },
  });
});

Deno.test("price.deleted mirrors active:false even though Stripe's payload omits the field", async () => {
  const admin = fakeAdmin();
  await applyEvent(admin, {
    type: "price.deleted",
    data: {
      object: {
        id: "price_1",
        product: "prod_1",
        // No `active` key at all -- mirrors Stripe's real deleted payload.
      },
    },
  });
  const call = admin.calls.find((c) =>
    c.table === "prices" && c.op === "upsert"
  );
  assertEquals(call.row.active, false);
});

Deno.test("checkout.session.completed backfills tenants.stripe_customer_id", async () => {
  const admin = fakeAdmin();
  await applyEvent(admin, {
    type: "checkout.session.completed",
    data: {
      object: {
        metadata: { tenant_id: "T1" },
        customer: "cus_123",
      },
    },
  });
  const call = admin.calls.find((c) =>
    c.table === "tenants" && c.op === "update"
  );
  assertEquals(call.row, { stripe_customer_id: "cus_123" });
  assertEquals(call.id, "T1");
});
