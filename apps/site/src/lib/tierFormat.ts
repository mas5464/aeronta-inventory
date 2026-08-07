// apps/site/src/lib/tierFormat.ts
//
// One formatter for tier prices, shared by pricing.astro and the homepage
// pricing teaser. Amounts come from the Supabase Stripe mirror in cents;
// null means "no self-serve price" (Enterprise / mirror unavailable).
export function formatTierPrice(amount: number | null, currency: string | null): string {
  if (amount == null) return "Contact us";
  const symbol = currency === "usd" ? "$" : currency ? `${currency.toUpperCase()} ` : "";
  return `${symbol}${(amount / 100).toLocaleString()}/mo`;
}
