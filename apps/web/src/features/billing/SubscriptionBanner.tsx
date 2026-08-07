import { useSubscription } from "@/lib/api/useSubscription";
import { cn } from "@/lib/utils";
import type { SubscriptionStatus } from "@/lib/api/billing";

/** Mirrors `BillingPage.tsx`'s `READONLY_STATUSES` — a lapsed subscription
 * where the guardrail blocks writeback until reactivated. */
const READONLY = new Set<SubscriptionStatus>(["canceled", "unpaid", "paused"]);

function messageFor(status: SubscriptionStatus, trialEndsAt: string | null): string | null {
  if (status === "trialing") {
    const ends = trialEndsAt ? new Date(trialEndsAt).toLocaleDateString() : null;
    return ends ? `Free trial — ends ${ends}.` : "Free trial in progress.";
  }
  if (status === "past_due") return "Payment failed — update your card to avoid interruption.";
  if (status && READONLY.has(status)) {
    return "Subscription lapsed — this workspace is read-only. Reactivate to resume.";
  }
  if (!status || status === "incomplete" || status === "incomplete_expired") {
    return "Finish subscribing to start using Aeronta.";
  }
  // "active" (and any other status we don't recognize) — nothing to say.
  return null;
}

export interface SubscriptionBannerProps {
  tenant: string;
}

/**
 * C4 Task 12 — a slim, dismissal-free strip mounted at the top of the authed
 * app shell (`App.tsx`), reading `useSubscription` (Task 9). Mirrors
 * `BillingPage.tsx`'s status grouping but as a compact nudge rather than a
 * full page: nothing for `active`; a trial countdown for `trialing`; an
 * update-card prompt for `past_due` (still writable — a grace period, per
 * `BillingPage.tsx`'s `ACTIVE_STATUSES`); a reactivate prompt for the
 * read-only terminal statuses; and a "finish subscribing" prompt for a
 * tenant that never completed Checkout (`null`/`incomplete*`).
 *
 * Deliberately silent on loading and on error — `data` is `undefined` in
 * both cases (no `initialData`), so a single `if (!data) return null` covers
 * both without a `QueryLoading`/`QueryError` flash on every route. This also
 * keeps it a no-op wherever the BFF has no `/billing` route configured
 * (e.g. auth-disabled local dev, where `App.tsx` doesn't mount this at all).
 */
export function SubscriptionBanner({ tenant }: SubscriptionBannerProps) {
  const { data } = useSubscription(tenant);
  if (!data) return null;

  const msg = messageFor(data.subscription_status, data.trial_ends_at);
  if (!msg) return null;

  const isTrial = data.subscription_status === "trialing";

  return (
    <div
      role="status"
      className={cn(
        "flex items-center justify-between gap-3 px-4 py-2 text-sm",
        isTrial ? "bg-cream text-ink" : "bg-bad/10 text-bad",
      )}
    >
      <span>{msg}</span>
      <a href="#/billing" className="shrink-0 font-medium underline-offset-2 hover:underline">
        Manage billing
      </a>
    </div>
  );
}
