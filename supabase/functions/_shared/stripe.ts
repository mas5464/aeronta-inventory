import Stripe from "npm:stripe@^16";

// Pinned to "2024-06-20" — the only literal stripe@16's bundled types accept
// (the version current when that package minor was cut). The brief's
// "2025-03-31.basil" does not type-check against this SDK version; rather
// than invent another string, pin to the one the installed types support so
// requests aren't silently subject to the account's dashboard-configured
// default API version (which can change independently of this code).
export const getStripe = () =>
  new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, {
    apiVersion: "2024-06-20",
  });
