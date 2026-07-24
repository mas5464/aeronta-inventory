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
} = {}) {
  const planQuota = opts.planQuota ??
    { growth: 25000, scale: 100000, starter: 5000 };
  const tenantsUpdateError = opts.tenantsUpdateError ?? null;
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
          eq: (_c: string, v: string) => ({
            maybeSingle: () => {
              if (table !== "plan_tiers") return { data: null, error: null };
              const key_quota = planQuota[v];
              return {
                data: key_quota === undefined ? null : { key_quota },
                error: null,
              };
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
