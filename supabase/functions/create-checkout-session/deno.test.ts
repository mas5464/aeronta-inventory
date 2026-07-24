import { assertEquals } from "jsr:@std/assert@1";
import { handler } from "./index.ts";

// Minimal fakes: an admin client that returns a fixed tenant/membership, and a
// Stripe stub whose checkout.sessions.create echoes its args.
function fakeDeps(
  { customerId = null, updateError = null }: {
    customerId?: string | null;
    updateError?: { message: string } | null;
  } = {},
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
        update: () => ({ eq: () => ({ error: updateError }) }),
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

// Builds the request; claims (when given) travel via the handler's `deps`
// seam, never via a header — see index.ts's `claimsFromAuthHeader` for the
// production path this bypasses in tests.
function req(
  body: unknown,
  headers: Record<string, string> = {},
) {
  return new Request("http://x", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

Deno.test("owner gets a checkout url; a new customer is created and stored", async () => {
  const { admin, stripe, created } = fakeDeps({ customerId: null });
  const res = await handler(
    req({ price_id: "price_growth" }),
    {
      admin,
      stripe,
      claims: { sub: "u1", tenant_id: "T1", tenant_role: "owner" },
    },
  );
  assertEquals(res.status, 200);
  assertEquals((await res.json()).url, "https://stripe/checkout");
  assertEquals(created.session.mode, "subscription");
  assertEquals(created.session.metadata.tenant_id, "T1");
  assertEquals(created.session.subscription_data.trial_period_days, 14);
  assertEquals(created.session.payment_method_collection, "always");
});

Deno.test("non-owner is 403", async () => {
  const { admin, stripe } = fakeDeps();
  const res = await handler(
    req({ price_id: "price_growth" }),
    {
      admin,
      stripe,
      claims: { sub: "u1", tenant_id: "T1", tenant_role: "planner" },
    },
  );
  assertEquals(res.status, 403);
});

Deno.test("401 when deps.claims is absent and there's no (or a garbage) Authorization header", async () => {
  const { admin, stripe } = fakeDeps();

  // No `claims` key on deps at all ⇒ handler falls back to decoding the
  // Authorization header, same as the real production path.
  const noHeader = await handler(
    req({ price_id: "price_growth" }),
    { admin, stripe },
  );
  assertEquals(noHeader.status, 401);

  const garbageHeader = await handler(
    req({ price_id: "price_growth" }, { Authorization: "Bearer not.a.jwt" }),
    { admin, stripe },
  );
  assertEquals(garbageHeader.status, 401);
});

Deno.test("500 when persisting the new stripe_customer_id fails; no checkout session is created", async () => {
  const { admin, stripe, created } = fakeDeps({
    customerId: null,
    updateError: { message: "db unavailable" },
  });
  const res = await handler(
    req({ price_id: "price_growth" }),
    {
      admin,
      stripe,
      claims: { sub: "u1", tenant_id: "T1", tenant_role: "owner" },
    },
  );
  assertEquals(res.status, 500);
  assertEquals((await res.json()).error, "failed to persist customer");
  assertEquals(created.session, undefined);
});

Deno.test("400 on malformed JSON body", async () => {
  const { admin, stripe } = fakeDeps();
  const res = await handler(
    req("{not valid json", {}),
    {
      admin,
      stripe,
      claims: { sub: "u1", tenant_id: "T1", tenant_role: "owner" },
    },
  );
  assertEquals(res.status, 400);
});
