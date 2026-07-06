const SAVINGS_COMPONENT_LABELS: Record<string, string> = {
  holding_cost_delta: "Holding cost",
  ordering_cost_delta: "Ordering cost",
  stockout_risk_delta: "Stockout risk",
};

/**
 * Human label for a savings `ProjectedComponent.name` (raw snake_case on the
 * wire). Falls back to title-casing the key so an unknown component is never
 * rendered as a raw snake_case string to users.
 */
export function savingsComponentLabel(name: string): string {
  return (
    SAVINGS_COMPONENT_LABELS[name] ??
    name.split("_").map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(" ")
  );
}

/** A 0-1 rate as a one-decimal percentage, e.g. 0.5 -> "50.0%". */
export function formatRatePct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * A BFF Decimal-string amount displayed with a `$` prefix. NOT parsed to a
 * float — the string is already correctly formatted server-side (avoids the
 * float-precision issue the UX audit flagged in the integer formatter).
 */
export function formatAmount(amount: string): string {
  return `$${amount}`;
}
