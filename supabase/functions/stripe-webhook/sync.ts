// Pure mirror/tenant sync. No HTTP, no signature — unit-testable.
//
// Write-error contract: every admin.from(...).upsert/update/select call below
// is checked for `{error}` and THROWS on failure. The caller (index.ts's
// handler) catches that throw and returns 500 so Stripe retries the
// delivery — silently swallowing a write error here would let the mirror /
// tenant state silently drift from Stripe's own records. See the C4 Task 6
// security review.
// deno-lint-ignore-file no-explicit-any
export async function applyEvent(admin: any, event: any): Promise<void> {
  const o = event.data.object;
  switch (event.type) {
    case "product.created":
    case "product.updated":
    case "product.deleted": {
      const { error } = await admin.from("products").upsert({
        id: o.id,
        // Stripe's `product.deleted` payload omits `active` entirely (the
        // deletion itself is the signal, not a field on the object), so
        // force it to false explicitly rather than falling through to the
        // `?? true` default -- otherwise the row stays `active: true` and
        // keeps showing up under the public pricing RLS (`using (active)`).
        active: event.type === "product.deleted" ? false : (o.active ?? true),
        name: o.name ?? null,
        description: o.description ?? null,
        metadata: o.metadata ?? {},
      });
      if (error) throw new Error(`products upsert failed: ${describe(error)}`);
      return;
    }
    case "price.created":
    case "price.updated":
    case "price.deleted": {
      const { error } = await admin.from("prices").upsert({
        id: o.id,
        product_id: o.product,
        // Same `*.deleted` omits-`active` gap as products above.
        active: event.type === "price.deleted" ? false : (o.active ?? true),
        unit_amount: o.unit_amount ?? null,
        currency: o.currency ?? null,
        interval: o.recurring?.interval ?? null,
        interval_count: o.recurring?.interval_count ?? 1,
        trial_period_days: o.recurring?.trial_period_days ?? null,
        metadata: o.metadata ?? {},
      });
      if (error) throw new Error(`prices upsert failed: ${describe(error)}`);
      return;
    }
    case "customer.subscription.created":
    case "customer.subscription.updated":
    case "customer.subscription.deleted": {
      const price = o.items?.data?.[0]?.price;
      let tenantId: string | null = o.metadata?.tenant_id ?? null;

      // Stripe does NOT copy Checkout Session metadata onto the Subscription
      // it creates, so a subscription event can arrive with no
      // `metadata.tenant_id` (e.g. subscriptions created before the
      // create-checkout-session fix, or created out-of-band). Fall back to
      // resolving the tenant by the Stripe customer id. A lookup ERROR is a
      // real failure and throws; a missing match means the event is simply
      // un-attributable -- skip every write below (never let a NULL
      // tenant_id reach the subscriptions insert) and ack 200 rather than
      // fail closed and force a retry that can never succeed.
      if (!tenantId && o.customer) {
        const { data, error: tenantLookupError } = await admin.from("tenants")
          .select("id").eq("stripe_customer_id", o.customer).maybeSingle();
        if (tenantLookupError) {
          throw new Error(
            `tenants lookup by stripe_customer_id failed: ${
              describe(tenantLookupError)
            }`,
          );
        }
        tenantId = data?.id ?? null;
      }
      if (!tenantId) return;

      const { error: subError } = await admin.from("subscriptions").upsert({
        id: o.id,
        tenant_id: tenantId,
        status: o.status,
        price_id: price?.id ?? null,
        quantity: o.items?.data?.[0]?.quantity ?? 1,
        cancel_at_period_end: o.cancel_at_period_end ?? false,
        current_period_end: o.current_period_end
          ? new Date(o.current_period_end * 1000).toISOString()
          : null,
        trial_end: o.trial_end
          ? new Date(o.trial_end * 1000).toISOString()
          : null,
      });
      if (subError) {
        throw new Error(`subscriptions upsert failed: ${describe(subError)}`);
      }

      // Resolve price.metadata.tier -> plan_tiers row. A lookup ERROR is a
      // real failure and throws. A missing row (no plan_tiers match for this
      // tier — `data === null`) is NOT an error: it just means we can't
      // resolve plan_tier/key_quota, so those two keys are omitted from the
      // patch below while the status fields still sync.
      const tier = price?.metadata?.tier ?? null;
      let keyQuota: number | null = null;
      let planTier: string | null = null;
      if (tier) {
        const { data, error: tierError } = await admin.from("plan_tiers")
          .select("key_quota").eq("tier", tier).maybeSingle();
        if (tierError) {
          throw new Error(`plan_tiers lookup failed: ${describe(tierError)}`);
        }
        if (data) {
          planTier = tier;
          keyQuota = data.key_quota ?? null;
        }
      }

      const patch: Record<string, unknown> = {
        subscription_status: o.status,
        stripe_subscription_id: o.id,
        current_period_end: o.current_period_end
          ? new Date(o.current_period_end * 1000).toISOString()
          : null,
        trial_ends_at: o.trial_end
          ? new Date(o.trial_end * 1000).toISOString()
          : null,
      };
      if (planTier) {
        patch.plan_tier = planTier;
        patch.key_quota = keyQuota;
      }
      const { error: tenantsError } = await admin.from("tenants")
        .update(patch).eq("id", tenantId);
      if (tenantsError) {
        throw new Error(`tenants update failed: ${describe(tenantsError)}`);
      }
      return;
    }
    case "checkout.session.completed": {
      if (o.metadata?.tenant_id && o.customer) {
        const { error } = await admin.from("tenants")
          .update({ stripe_customer_id: o.customer })
          .eq("id", o.metadata.tenant_id);
        if (error) {
          throw new Error(
            `tenants stripe_customer_id backfill failed: ${describe(error)}`,
          );
        }
      }
      return;
    }
    default:
      return; // ack-and-ignore — unhandled event types are not failures
  }
}

function describe(error: unknown): string {
  if (error && typeof error === "object") {
    const e = error as { message?: string; code?: string };
    return e.message ?? e.code ?? JSON.stringify(error);
  }
  return String(error);
}
