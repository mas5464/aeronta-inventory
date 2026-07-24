import { request } from "@/lib/api/client";
import { supabase } from "@/lib/auth/supabase";

/**
 * Billing API client (C4 Task 9): reads over the BFF's tenant-scoped
 * `/billing` route (Task 8), plus two edge-function callers that hit the
 * Stripe-fronting Edge Functions directly (Tasks 4–5) — those aren't BFF
 * routes, so they use a bare `fetch` against `functionsBaseUrl()` rather
 * than the shared `request<T>()` helper (which targets `BASE_URL`, the BFF).
 *
 * Mirrors services/agent-spine/src/trax_io_spine/bff's `/v1/tenants/{t}/billing`
 * response shape and the `create-checkout-session`/`create-portal-link`
 * Supabase Edge Functions' request/response contracts.
 */

export type SubscriptionStatus =
  | "trialing"
  | "active"
  | "past_due"
  | "canceled"
  | "incomplete"
  | "incomplete_expired"
  | "unpaid"
  | "paused"
  | null;

export interface BillingSummary {
  plan_tier: string;
  subscription_status: SubscriptionStatus;
  key_quota: number;
  keys_used: number;
  current_period_end: string | null;
  trial_ends_at: string | null;
}

/** Base URL for this tenant's Supabase project's Edge Functions. Empty
 * `VITE_SUPABASE_URL` (auth-disabled dev) yields `/functions/v1` — callers
 * degrade the same way `request()` does for an empty `VITE_BFF_URL`. */
export function functionsBaseUrl(): string {
  const base = (import.meta.env.VITE_SUPABASE_URL as string | undefined) ?? "";
  return `${base.replace(/\/$/, "")}/functions/v1`;
}

export function getBilling(tenant: string): Promise<BillingSummary> {
  return request<BillingSummary>(`/v1/tenants/${encodeURIComponent(tenant)}/billing`);
}

async function callFunction(name: string, body: unknown): Promise<{ url: string }> {
  const session = (await supabase?.auth.getSession())?.data.session;
  const token = session?.access_token;
  const res = await fetch(`${functionsBaseUrl()}/${name}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${name} failed: ${res.status}`);
  return res.json();
}

/** `tenant` isn't sent to the function — it's derived server-side from the
 * caller's JWT — but is kept as a param for call-site symmetry with the
 * rest of this module (every other export here takes `tenant` first). */
export async function createCheckoutSession(_tenant: string, priceId: string): Promise<string> {
  return (await callFunction("create-checkout-session", { price_id: priceId })).url;
}

export async function createPortalLink(_tenant: string): Promise<string> {
  return (await callFunction("create-portal-link", {})).url;
}

/** A publicly-visible Stripe price row, as surfaced by the `prices` table's
 * public-read RLS policy (`active` prices only — see
 * supabase/migrations/20260723000011_billing_stripe_mirror.sql). */
export interface PublicPrice {
  id: string;
  product_id: string | null;
  unit_amount: number | null;
  currency: string | null;
  interval: string | null;
  tier: string | null;
}

/**
 * Reads active prices via the anon Supabase client — used by the pricing /
 * signup surfaces (Tasks 10/11) to render plan cards without a BFF round
 * trip. Degrades to `[]` when `supabase` is null (auth-disabled dev), same
 * as every other Supabase-backed consumer in this app.
 */
export async function getPublicPrices(): Promise<PublicPrice[]> {
  if (!supabase) return [];
  const { data, error } = await supabase
    .from("prices")
    .select("id, product_id, unit_amount, currency, interval, metadata")
    .eq("active", true);
  if (error) throw error;
  return (data ?? []).map((row) => ({
    id: row.id as string,
    product_id: (row.product_id as string | null) ?? null,
    unit_amount: (row.unit_amount as number | null) ?? null,
    currency: (row.currency as string | null) ?? null,
    interval: (row.interval as string | null) ?? null,
    tier: ((row.metadata as Record<string, unknown> | null)?.tier as string | undefined) ?? null,
  }));
}
