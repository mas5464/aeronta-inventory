import { assertEquals } from "jsr:@std/assert@1";
import { handler } from "./index.ts";

// Minimal fakes: an admin client that returns a fixed tenant/membership, and a
// Stripe stub whose checkout.sessions.create echoes its args.
function fakeDeps(
  { customerId = null }: { role?: string; customerId?: string | null } = {},
) {
  // The handler only ever queries `tenants` — a single `.select().eq().maybeSingle()`
  // chain (role gating is decided entirely off the JWT's tenant_role claim, no
  // `memberships` lookup) — so the fake need only support that one chain shape.
  const admin = {
    from(_table: string) {
      return {
        select: () => ({
          eq: () => ({
            maybeSingle: () => ({
              data: { stripe_customer_id: customerId, id: "T1" },
            }),
          }),
        }),
        update: () => ({ eq: () => ({ error: null }) }),
      };
    },
  };
  // deno-lint-ignore no-explicit-any -- captures whatever shape each fake echoes back for assertions
  const created: any = {};
  const stripe = {
    customers: {
      create: (a: unknown) => {
        created.customer = a;
        return { id: "cus_new" };
      },
    },
    checkout: {
      sessions: {
        create: (a: unknown) => {
          created.session = a;
          return { url: "https://stripe/checkout" };
        },
      },
    },
  };
  return { admin, stripe, created };
}

function req(body: unknown, claims: Record<string, unknown>) {
  // The handler trusts an already-verified JWT: it decodes claims from a header
  // the Supabase functions runtime sets (x-user-claims) OR verifies the bearer.
  return new Request("http://x", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-test-claims": JSON.stringify(claims),
    },
    body: JSON.stringify(body),
  });
}

Deno.test("owner gets a checkout url; a new customer is created and stored", async () => {
  const deps = fakeDeps({ role: "owner", customerId: null });
  const res = await handler(
    req({ price_id: "price_growth" }, {
      sub: "u1",
      tenant_id: "T1",
      tenant_role: "owner",
    }),
    deps,
  );
  assertEquals(res.status, 200);
  assertEquals((await res.json()).url, "https://stripe/checkout");
  assertEquals(deps.created.session.subscription_data.trial_period_days, 14);
  assertEquals(deps.created.session.payment_method_collection, "always");
});

Deno.test("non-owner is 403", async () => {
  const deps = fakeDeps({ role: "planner" });
  const res = await handler(
    req({ price_id: "price_growth" }, {
      sub: "u1",
      tenant_id: "T1",
      tenant_role: "planner",
    }),
    deps,
  );
  assertEquals(res.status, 403);
});
