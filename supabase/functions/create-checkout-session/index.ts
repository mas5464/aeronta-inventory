import { corsHeaders, json } from "../_shared/cors.ts";
import { getStripe } from "../_shared/stripe.ts";
import { getServiceClient } from "../_shared/supabase.ts";
import { claimsFromAuthHeader } from "../_shared/claims.ts";

export async function handler(
  req: Request,
  // Injectable seam over the real Stripe SDK, SupabaseClient, and the caller's
  // verified claims (prod) or hand-rolled test fakes (tests); a precise
  // structural type here would have to track both, which is more coupling
  // than the seam is meant to carry. `claims` defaults to decoding the
  // Authorization header — see claims.ts for the verify_jwt=true invariant
  // this relies on.
  // deno-lint-ignore no-explicit-any
  deps?: { stripe: any; admin: any; claims?: Record<string, unknown> | null },
) {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }
  const claims = deps && "claims" in deps
    ? deps.claims
    : claimsFromAuthHeader(req);
  if (!claims?.sub || !claims?.tenant_id) {
    return json({ error: "unauthenticated" }, 401);
  }
  if (claims.tenant_role !== "owner") {
    return json({ error: "owner required" }, 403);
  }

  let price_id: string | undefined;
  try {
    ({ price_id } = await req.json());
  } catch {
    return json({ error: "malformed request body" }, 400);
  }
  if (!price_id) return json({ error: "price_id required" }, 400);

  const stripe = deps?.stripe ?? getStripe();
  const admin = deps?.admin ?? getServiceClient();

  const { data: tenant } = await admin.from("tenants")
    .select("id, stripe_customer_id").eq("id", claims.tenant_id).maybeSingle();
  if (!tenant) return json({ error: "tenant not found" }, 404);

  let customerId = tenant.stripe_customer_id;
  if (!customerId) {
    const cust = await stripe.customers.create({
      metadata: { tenant_id: tenant.id },
    });
    customerId = cust.id;
    const { error } = await admin.from("tenants").update({
      stripe_customer_id: customerId,
    }).eq("id", tenant.id);
    if (error) {
      return json({ error: "failed to persist customer" }, 500);
    }
  }

  const appOrigin = Deno.env.get("APP_ORIGIN") ?? "http://localhost:5173";
  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    customer: customerId,
    line_items: [{ price: price_id, quantity: 1 }],
    // Stripe does NOT copy Checkout Session metadata onto the Subscription
    // it creates, so `customer.subscription.created`/`.updated` webhooks
    // would otherwise arrive with no `metadata.tenant_id` -- set it here too
    // (see sync.ts's customer-id fallback for events that predate this fix).
    subscription_data: {
      trial_period_days: 14,
      metadata: { tenant_id: tenant.id },
    },
    payment_method_collection: "always",
    metadata: { tenant_id: tenant.id },
    success_url: `${appOrigin}/#/billing?checkout=success`,
    cancel_url: `${appOrigin}/#/billing?checkout=cancel`,
  });
  return json({ url: session.url });
}

// Guarded so `import { handler }` in deno.test.ts doesn't also bind a listener
// (it collides with anything already on the default port, e.g. local Docker).
if (import.meta.main) {
  Deno.serve((req) => handler(req));
}
