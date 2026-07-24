// deno-lint-ignore-file no-explicit-any -- the `deps` seam below is a
// structural stand-in for the Stripe SDK / SupabaseClient / applyEvent (prod)
// or hand-rolled test fakes (tests); a precise type would have to track both,
// which is more coupling than the seam is meant to carry. Mirrors
// create-checkout-session/create-portal-link's `deps` pattern.
import { getStripe } from "../_shared/stripe.ts";
import { getServiceClient } from "../_shared/supabase.ts";
import { applyEvent as realApply } from "./sync.ts";

// PUBLIC endpoint (config.toml: verify_jwt = false) — the Stripe signature
// check below (`stripe.webhooks.constructEventAsync`) IS the auth for this
// function. Never skip it, and never fall back to a known default secret:
// if STRIPE_WEBHOOK_SIGNING_SECRET isn't configured we fail closed (500)
// rather than risk verifying a forged payload against a guessable value.
export async function handler(
  req: Request,
  deps?: {
    admin: any;
    stripe: any;
    applyEvent?: (a: any, e: any) => Promise<void>;
  },
) {
  const admin = deps?.admin ?? getServiceClient();
  const stripe = deps?.stripe ?? getStripe();
  const apply = deps?.applyEvent ?? realApply;

  const secret = Deno.env.get("STRIPE_WEBHOOK_SIGNING_SECRET");
  if (!secret) {
    return new Response("webhook secret not configured", { status: 500 });
  }

  const body = await req.text();
  const sig = req.headers.get("stripe-signature") ?? "";
  let event: any;
  try {
    event = await stripe.webhooks.constructEventAsync(body, sig, secret);
  } catch {
    return new Response("bad signature", { status: 400 });
  }

  // Idempotency: insert event.id into the stripe_events ledger; a
  // unique-violation means we already processed this event. supabase-js
  // error shapes vary by PostgREST version, so treat either the canonical
  // Postgres unique-violation code or a message mentioning "duplicate key"
  // as a dup. Any OTHER insert error is a real failure -> 500 (Stripe
  // retries the delivery).
  const { error } = await admin.from("stripe_events").insert({
    id: event.id,
    type: event.type,
  });
  if (error) {
    if (isDuplicateKeyError(error)) return new Response("dup", { status: 200 });
    return new Response("event log error", { status: 500 });
  }

  try {
    await apply(admin, event);
  } catch {
    // Poison-message hazard: the stripe_events row above was already
    // inserted, so leaving it in place after a failed apply means Stripe's
    // retry (triggered by this 500) hits the dup-detection insert, gets an
    // early 200, and the event is silently dropped forever. Delete the row
    // we just inserted so the retry re-processes the event from scratch.
    // If this delete itself fails, still return 500 (nothing sensitive in
    // the body either way): the event id stays in stripe_events and
    // Stripe's retry will dup->200 without re-applying -- the rare
    // double-failure (apply throws AND delete fails) loses the event;
    // acceptable residual, monitor 500s.
    await admin.from("stripe_events").delete().eq("id", event.id);
    return new Response("apply error", { status: 500 }); // Stripe retries
  }
  return new Response("ok", { status: 200 });
}

function isDuplicateKeyError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const e = error as { code?: string; message?: string };
  return e.code === "23505" ||
    (typeof e.message === "string" && e.message.includes("duplicate key"));
}

// Guarded so `import { handler }` in the test files doesn't also bind a
// listener (it collides with anything already on the default port, e.g.
// local Docker) — mirrors create-checkout-session/create-portal-link.
if (import.meta.main) {
  Deno.serve((req) => handler(req));
}
