// deno-lint-ignore-file no-explicit-any require-await -- the fakes below
// deliberately mirror the untyped `deps` seam (see index.ts), and several
// stub async methods have no internal `await` since they only need to
// satisfy a Promise-returning contract, not actually do async work.
import { assertEquals } from "jsr:@std/assert@1";
import { handler } from "./index.ts";
import { applyEvent } from "./sync.ts";

// The webhook secret is only ever used as a fail-closed gate here (see
// index.ts) and as an opaque argument to the stubbed Stripe client below,
// which ignores its value entirely — any non-empty string satisfies both.
Deno.env.set("STRIPE_WEBHOOK_SIGNING_SECRET", "whsec_test");

function deps({ seen = new Set<string>() } = {}) {
  const applied: string[] = [];
  const admin = {
    from: (t: string) => ({
      insert: (r: any) => {
        if (t === "stripe_events") {
          if (seen.has(r.id)) return { error: { code: "23505" } };
          seen.add(r.id);
        }
        return { error: null };
      },
    }),
  };
  // Stub Stripe signature verification: valid unless body contains "BAD".
  const stripe = {
    webhooks: {
      constructEventAsync: async (body: string) => {
        if (body.includes("BAD")) throw new Error("bad sig");
        return { id: JSON.parse(body).id, type: "ping", data: { object: {} } };
      },
    },
  };
  return {
    admin,
    stripe,
    applyEvent: async (_a: any, e: any) => {
      applied.push(e.id);
    },
    applied,
  };
}
const req = (body: string) =>
  new Request("http://x", {
    method: "POST",
    headers: { "stripe-signature": "sig" },
    body,
  });

Deno.test("bad signature -> 400, no apply", async () => {
  const d = deps();
  const res = await handler(req('{"id":"evt_BAD"}'), d);
  assertEquals(res.status, 400);
  assertEquals(d.applied.length, 0);
});

Deno.test("duplicate event.id -> processed once", async () => {
  const d = deps();
  await handler(req('{"id":"evt_1"}'), d);
  await handler(req('{"id":"evt_1"}'), d);
  assertEquals(d.applied.length, 1);
});

Deno.test("duplicate detection also matches a non-postgrest 'duplicate key' message (no code field)", async () => {
  const seen = new Set<string>();
  const applied: string[] = [];
  const admin = {
    from: (t: string) => ({
      insert: (r: any) => {
        if (t !== "stripe_events") return { error: null };
        if (seen.has(r.id)) {
          return {
            error: { message: "duplicate key value violates constraint" },
          };
        }
        seen.add(r.id);
        return { error: null };
      },
    }),
  };
  const stripe = {
    webhooks: {
      constructEventAsync: async (body: string) => ({
        id: JSON.parse(body).id,
        type: "ping",
        data: { object: {} },
      }),
    },
  };
  const apply = async (_a: any, e: any) => {
    applied.push(e.id);
  };
  const res1 = await handler(req('{"id":"evt_dup2"}'), {
    admin,
    stripe,
    applyEvent: apply,
  });
  const res2 = await handler(req('{"id":"evt_dup2"}'), {
    admin,
    stripe,
    applyEvent: apply,
  });
  assertEquals(res1.status, 200);
  assertEquals(res2.status, 200);
  assertEquals(applied.length, 1);
});

Deno.test("missing webhook secret -> 500, fail closed (never verifies against a default)", async () => {
  const prior = Deno.env.get("STRIPE_WEBHOOK_SIGNING_SECRET");
  Deno.env.delete("STRIPE_WEBHOOK_SIGNING_SECRET");
  try {
    const d = deps();
    const res = await handler(req('{"id":"evt_nosecret"}'), d);
    assertEquals(res.status, 500);
    assertEquals(d.applied.length, 0);
  } finally {
    if (prior !== undefined) {
      Deno.env.set("STRIPE_WEBHOOK_SIGNING_SECRET", prior);
    }
  }
});

Deno.test("a tenants-update error inside applyEvent propagates -> handler returns 500 (Stripe retries)", async () => {
  // Uses the REAL applyEvent (not the stub above) to prove the sync.ts throw
  // on a failed tenants update surfaces through the handler's catch as a 500.
  const stripeEventIds = new Set<string>();
  const admin = {
    from: (t: string): any => {
      if (t === "stripe_events") {
        return {
          insert: (r: any) => {
            if (stripeEventIds.has(r.id)) return { error: { code: "23505" } };
            stripeEventIds.add(r.id);
            return { error: null };
          },
        };
      }
      if (t === "subscriptions") {
        return { upsert: () => ({ error: null }) };
      }
      if (t === "tenants") {
        return {
          update: (_row: any) => ({
            eq: (_c: string, _v: string) => ({
              error: { message: "db unavailable" },
            }),
          }),
        };
      }
      throw new Error(`unexpected table ${t}`);
    },
  };
  const stripe = {
    webhooks: {
      constructEventAsync: async (body: string) => ({
        id: JSON.parse(body).id,
        type: "customer.subscription.updated",
        data: {
          object: {
            id: "sub_1",
            status: "active",
            metadata: { tenant_id: "T1" },
            items: { data: [{ price: { id: "price_1", metadata: {} } }] },
            current_period_end: 1893456000,
            trial_end: null,
            cancel_at_period_end: false,
          },
        },
      }),
    },
  };
  const res = await handler(req('{"id":"evt_tenants_fail"}'), {
    admin,
    stripe,
    applyEvent,
  });
  assertEquals(res.status, 500);
});
