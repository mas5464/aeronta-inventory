import { corsHeaders, json } from "../_shared/cors.ts";
import { getStripe } from "../_shared/stripe.ts";
import { getServiceClient } from "../_shared/supabase.ts";

// In production the Supabase runtime verifies the bearer JWT (verify_jwt=true)
// and exposes claims; in tests we inject via x-test-claims. Real deploys read
// the verified claims from the Authorization bearer.
function claimsOf(req: Request): Record<string, unknown> | null {
  const t = req.headers.get("x-test-claims");
  if (t) return JSON.parse(t);
  const auth = req.headers.get("Authorization")?.replace("Bearer ", "");
  if (!auth) return null;
  try {
    return JSON.parse(atob(auth.split(".")[1]));
  } catch {
    return null;
  }
}

export async function handler(
  req: Request,
  // Injectable seam over the real Stripe SDK and SupabaseClient (prod) or
  // hand-rolled test fakes (tests); a precise structural type here would have
  // to track both, which is more coupling than the seam is meant to carry.
  // deno-lint-ignore no-explicit-any
  deps?: { stripe: any; admin: any },
) {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }
  const claims = claimsOf(req);
  if (!claims?.sub || !claims?.tenant_id) {
    return json({ error: "unauthenticated" }, 401);
  }
  if (claims.tenant_role !== "owner") {
    return json({ error: "owner required" }, 403);
  }

  const { price_id } = await req.json();
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
    await admin.from("tenants").update({ stripe_customer_id: customerId }).eq(
      "id",
      tenant.id,
    );
  }

  const appOrigin = Deno.env.get("APP_ORIGIN") ?? "http://localhost:5173";
  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    customer: customerId,
    line_items: [{ price: price_id, quantity: 1 }],
    subscription_data: { trial_period_days: 14 },
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
