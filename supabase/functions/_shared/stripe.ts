import Stripe from "npm:stripe@^16";

// NOTE: no `apiVersion` pin here. stripe@16's bundled types lock `apiVersion`
// to the literal "2024-06-20" (the version current when that package minor
// was cut) and reject any other string at compile time — including the
// brief's "2025-03-31.basil". Per plan: drop the pin rather than invent a
// version string; the account's dashboard-configured default API version
// applies instead.
export const getStripe = () => new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!);
