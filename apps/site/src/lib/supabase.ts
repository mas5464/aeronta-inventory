// apps/site/src/lib/supabase.ts
//
// Build-time-only data access for the marketing site. Astro runs this module
// during `astro build` (static output, no server runtime) — every export here
// is invoked from page frontmatter and resolved before HTML is emitted.
//
// No env (PUBLIC_SUPABASE_URL / PUBLIC_SUPABASE_ANON_KEY unset) → `supabase`
// is null and getPricingTiers() short-circuits to []. The pricing page must
// still render (Enterprise-only grid) in that case — never throw.
//
// Network/query failure at build time (bad URL, unreachable host, schema
// drift) must also degrade to [] rather than fail `astro build` — a stale or
// unreachable Supabase mirror should not take the marketing site down.
import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.PUBLIC_SUPABASE_URL;
const anon = import.meta.env.PUBLIC_SUPABASE_ANON_KEY;

function buildClient() {
  if (!url || !anon) return null;
  try {
    return createClient(url, anon, {
      // This module only ever does one-shot REST reads at build time — it
      // never opens a realtime socket. supabase-js's constructor eagerly
      // resolves a WebSocket implementation for its (unused) Realtime
      // client and *throws synchronously* under Node < 22, which has no
      // native WebSocket global. Supplying any transport here short-
      // circuits that lookup; it is never actually invoked since nothing
      // in this module calls `.channel()`/`.connect()`.
      realtime: { transport: (() => {}) as unknown as never },
    });
  } catch (err) {
    // Defensive: any other construction-time throw (e.g. a malformed URL)
    // must degrade to null too, not fail `astro build`.
    console.warn("[site] createClient failed — falling back to no client —", err);
    return null;
  }
}

export const supabase = buildClient();

export type Tier = {
  tier: string;
  display_name: string;
  key_quota: number;
  unit_amount: number | null;
  currency: string | null;
  interval: string | null;
};

export async function getPricingTiers(): Promise<Tier[]> {
  if (!supabase) return []; // build without env → empty; page still renders

  try {
    const { data: tiers, error: tiersError } = await supabase
      .from("plan_tiers")
      .select("tier,display_name,key_quota,sort")
      .order("sort");
    if (tiersError) throw tiersError;

    const { data: prices, error: pricesError } = await supabase
      .from("prices")
      .select("unit_amount,currency,interval,metadata")
      .eq("active", true);
    if (pricesError) throw pricesError;

    return (tiers ?? []).map((t: any) => {
      const p = (prices ?? []).find(
        (x: any) => x.metadata?.tier === t.tier && x.interval === "month",
      );
      return {
        ...t,
        unit_amount: p?.unit_amount ?? null,
        currency: p?.currency ?? null,
        interval: p?.interval ?? "month",
      };
    });
  } catch (err) {
    // Build-time fetch failure (unreachable host, DNS, schema drift, etc.)
    // must never fail the static build — degrade to an empty tier list.
    console.warn("[site] getPricingTiers: falling back to [] —", err);
    return [];
  }
}
