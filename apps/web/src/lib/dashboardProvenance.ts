import type { Provenance } from "@/lib/provenance";

/**
 * Provenance stamp for `DashboardSummary` aggregates.
 *
 * The dashboard is computed by the recommendation engine from the nightly
 * eMRO extract (see services/agent-spine/src/trax_io_spine/bff/store.py —
 * `PlannerStore.dashboard()` derives from the same extract-seeded data as
 * the queue). There is no per-field provenance on the wire yet for these
 * aggregates, so we honestly stamp the whole summary with its true origin:
 * the eMRO nightly extract, feed INVENTORY, as of "now" (the extract this
 * store instance was seeded from). This is the FeedId/systemOfRecord
 * vocabulary fixed by docs/DATA-MODEL.md §2.
 */
export function dashboardProvenance(asOf: Date = new Date()): Provenance {
  return {
    source: "eMRO Nightly Extract",
    systemOfRecord: "INVENTORY",
    freshnessAt: asOf.toISOString(),
    coverage: 1,
    confidence: 0.95,
    derived: true,
  };
}
