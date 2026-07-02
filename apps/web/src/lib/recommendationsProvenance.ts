import type { Provenance } from "@/lib/provenance";

/**
 * Provenance stamp for recommendation-queue / detail fields (Slice S3 —
 * Workbench + AI Recommendations).
 *
 * `QueueRow` / `RecommendationDetail` (services/agent-spine/src/trax_io_spine/
 * bff/models.py) are produced by the Trax Optimizer (recommendation engine +
 * guardrail) from the same Feature Store reads as the Part Drill-Down, but
 * the recommendation itself — confidence, priority, cost impact — is a
 * derived output of the optimizer, not a raw feed read. There is no
 * per-field provenance on the wire yet, so — same honesty standard as
 * dashboardProvenance.ts / partProvenance.ts — we stamp every surfaced
 * recommendation value with its true origin: the Trax Optimizer, as of the
 * recommendation's own `as_of` (falls back to "now" when absent).
 */
export function recommendationProvenance(asOf: Date = new Date()): Provenance {
  return {
    source: "Trax Optimizer",
    systemOfRecord: "RECOMMENDATIONS",
    freshnessAt: asOf.toISOString(),
    coverage: 1,
    confidence: 0.9,
    derived: true,
  };
}
