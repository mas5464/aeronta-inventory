import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { cn } from "@/lib/utils";
import { useSubscription } from "@/lib/api/useSubscription";
import {
  createCheckoutSession,
  createPortalLink,
  getPublicPrices,
  type SubscriptionStatus,
} from "@/lib/api/billing";

/** Mirrors the Stripe subscription lifecycle (C4 design): `trialing`/`active`/
 * `past_due` still let the tenant write (past_due is a grace period handled
 * server-side by the guardrail, not here); everything else is either not yet
 * provisioned (`null`/`incomplete*`) or a terminal, read-only state. */
const ACTIVE_STATUSES = new Set<SubscriptionStatus>(["trialing", "active", "past_due"]);
const READONLY_STATUSES = new Set<SubscriptionStatus>(["canceled", "unpaid", "paused"]);

type PlanState = "active" | "readonly" | "provisioning";

function planStateOf(status: SubscriptionStatus): PlanState {
  if (status && ACTIVE_STATUSES.has(status)) return "active";
  if (status && READONLY_STATUSES.has(status)) return "readonly";
  return "provisioning";
}

async function defaultPriceId(): Promise<string> {
  const prices = await getPublicPrices();
  const monthly = prices.find((p) => p.interval === "month") ?? prices[0];
  if (!monthly) throw new Error("No plans are available right now.");
  return monthly.id;
}

function actionErrorText(error: unknown): string | null {
  if (!error) return null;
  return error instanceof Error ? error.message : "Something went wrong";
}

export interface BillingPageProps {
  tenant: string;
  role: string | null;
}

/**
 * `/billing` — plan & usage (C4 Task 10). Reads `useSubscription` (Task 9,
 * over the BFF's `/v1/tenants/{tenant}/billing`) and renders three states
 * driven by `subscription_status`: **active** (`trialing`/`active`/
 * `past_due`, writes allowed), **read-only** (`canceled`/`unpaid`/`paused`,
 * the guardrail blocks writeback until reactivated), and **provisioning**
 * (`null`/`incomplete*`, no Stripe subscription yet). Billing actions
 * (Stripe Checkout/Portal) are owner-only — mirrors the BFF's own
 * owner-gate on the Stripe edge functions and the `members`-style nav gate
 * in `App.tsx`; non-owners get a read-only summary and a pointer to ask an
 * owner.
 */
export function BillingPage({ tenant, role }: BillingPageProps) {
  const { data, isPending, isError, error, refetch } = useSubscription(tenant);
  const [isRedirecting, setIsRedirecting] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);

  if (isPending) return <QueryLoading label="Loading billing…" />;
  if (isError) return <QueryError label="Failed to load billing" error={error} onRetry={() => refetch()} />;

  const summary = data;
  const state = planStateOf(summary.subscription_status);
  const isOwner = role === "owner";
  const pct = summary.key_quota > 0 ? Math.min(100, Math.round((summary.keys_used / summary.key_quota) * 100)) : 0;
  const overQuota = pct >= 100;

  async function goToPortal() {
    setActionError(null);
    setIsRedirecting(true);
    try {
      window.location.href = await createPortalLink(tenant);
    } catch (err) {
      setActionError(err);
      setIsRedirecting(false);
    }
  }

  async function startSubscription() {
    setActionError(null);
    setIsRedirecting(true);
    try {
      const priceId = await defaultPriceId();
      window.location.href = await createCheckoutSession(tenant, priceId);
    } catch (err) {
      setActionError(err);
      setIsRedirecting(false);
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <header>
        <h1 className="text-xl font-semibold text-ink">Billing &amp; usage</h1>
        <p className="text-sm text-ink-2">Manage your subscription and see how many keys you&apos;re using.</p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Plan</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1">
          <div className="text-lg font-semibold capitalize text-ink">{summary.plan_tier}</div>
          {summary.subscription_status && (
            <div className="text-sm text-ink-2">Status: {summary.subscription_status}</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Managed part-location keys</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <div className="text-sm text-ink">
            {summary.keys_used.toLocaleString("en-US")} / {summary.key_quota.toLocaleString("en-US")}
          </div>
          <div className="h-2 w-full rounded-full bg-panel-2" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div className={cn("h-2 rounded-full", overQuota ? "bg-bad" : "bg-brand")} style={{ width: `${pct}%` }} />
          </div>
          {overQuota && <p className="text-sm text-bad">Over quota — upgrade to ingest more keys.</p>}
        </CardContent>
      </Card>

      {state === "readonly" && (
        <p role="alert" className="text-sm text-bad">
          Your subscription has lapsed and this workspace is read-only. Reactivate to resume writes.
        </p>
      )}

      {isOwner ? (
        <div className="flex flex-col items-start gap-2">
          {state === "provisioning" ? (
            <Button onClick={() => void startSubscription()} disabled={isRedirecting}>
              Start subscription
            </Button>
          ) : (
            <Button onClick={() => void goToPortal()} disabled={isRedirecting}>
              Manage billing
            </Button>
          )}
          {actionErrorText(actionError) && (
            <p role="alert" className="text-xs text-bad">
              {actionErrorText(actionError)}
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm text-ink-2">Ask an owner to manage billing.</p>
      )}
    </div>
  );
}
