import { assertEquals } from "jsr:@std/assert@1";
import { handler } from "./index.ts";

// Minimal fake: an admin client returning a fixed tenant's stripe_customer_id,
// and a Stripe stub whose billingPortal.sessions.create echoes its args.
function fakeDeps(customerId: string | null) {
  const admin = {
    from(_table: string) {
      return {
        select: () => ({
          eq: () => ({
            maybeSingle: () => ({ data: { stripe_customer_id: customerId } }),
          }),
        }),
      };
    },
  };
  // deno-lint-ignore no-explicit-any -- captures whatever shape the stub echoes back for assertions
  const created: any = {};
  const stripe = {
    billingPortal: {
      sessions: {
        create: (a: unknown) => {
          created.session = a;
          return { url: "https://stripe/portal" };
        },
      },
    },
  };
  return { admin, stripe, created };
}

// Builds the request; claims (when given) travel via the handler's `deps`
// seam, never via a header — see index.ts's `claimsFromAuthHeader` for the
// production path this bypasses in tests.
function req(headers: Record<string, string> = {}) {
  return new Request("http://x", { method: "POST", headers });
}

Deno.test("owner with a customer gets a portal url", async () => {
  const { admin, stripe, created } = fakeDeps("cus_1");
  const res = await handler(req(), {
    admin,
    stripe,
    claims: { sub: "u1", tenant_id: "T1", tenant_role: "owner" },
  });
  assertEquals(res.status, 200);
  assertEquals((await res.json()).url, "https://stripe/portal");
  assertEquals(created.session.customer, "cus_1");
});

Deno.test("no customer yet -> 409", async () => {
  const { admin, stripe } = fakeDeps(null);
  const res = await handler(req(), {
    admin,
    stripe,
    claims: { sub: "u1", tenant_id: "T1", tenant_role: "owner" },
  });
  assertEquals(res.status, 409);
});

Deno.test("non-owner -> 403", async () => {
  const { admin, stripe } = fakeDeps("cus_1");
  const res = await handler(req(), {
    admin,
    stripe,
    claims: { sub: "u1", tenant_id: "T1", tenant_role: "viewer" },
  });
  assertEquals(res.status, 403);
});

Deno.test("401 when deps.claims is absent and there's no (or a garbage) Authorization header", async () => {
  const { admin, stripe } = fakeDeps("cus_1");

  // No `claims` key on deps at all ⇒ handler falls back to decoding the
  // Authorization header, same as the real production path.
  const noHeader = await handler(req(), { admin, stripe });
  assertEquals(noHeader.status, 401);

  const garbageHeader = await handler(
    req({ Authorization: "Bearer not.a.jwt" }),
    { admin, stripe },
  );
  assertEquals(garbageHeader.status, 401);
});
