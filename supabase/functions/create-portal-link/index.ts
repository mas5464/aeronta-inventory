import { corsHeaders, json } from "../_shared/cors.ts";
import { getStripe } from "../_shared/stripe.ts";
import { getServiceClient } from "../_shared/supabase.ts";
import { claimsFromAuthHeader } from "../_shared/claims.ts";

export async function handler(
  req: Request,
  // Injectable seam over the real Stripe SDK, SupabaseClient, and the caller's
  // verified claims (prod) or hand-rolled test fakes (tests) — mirrors
  // create-checkout-session/index.ts. `claims` defaults to decoding the
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

  const admin = deps?.admin ?? getServiceClient();
  const { data: tenant } = await admin.from("tenants")
    .select("stripe_customer_id").eq("id", claims.tenant_id).maybeSingle();
  if (!tenant?.stripe_customer_id) {
    return json({ error: "no customer" }, 409);
  }

  const stripe = deps?.stripe ?? getStripe();
  const appOrigin = Deno.env.get("APP_ORIGIN") ?? "http://localhost:5173";
  const portal = await stripe.billingPortal.sessions.create({
    customer: tenant.stripe_customer_id,
    return_url: `${appOrigin}/#/billing`,
  });
  return json({ url: portal.url });
}

// Guarded so `import { handler }` in deno.test.ts doesn't also bind a listener
// (it collides with anything already on the default port, e.g. local Docker).
if (import.meta.main) {
  Deno.serve((req) => handler(req));
}
