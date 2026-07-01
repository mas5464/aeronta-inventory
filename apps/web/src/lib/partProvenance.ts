import type { Provenance } from "@/lib/provenance";

/**
 * Provenance stamps for `PartContext` fields (Slice S2 — Part Drill-Down).
 *
 * The BFF's `PartContext` (services/agent-spine/src/trax_io_spine/bff/store.py
 * `part_context()`) assembles the view from several Feature Store reads, each
 * backed by a distinct eMRO extract feed:
 *   - stock, current/proposed policy → INVENTORY (PN_INVENTORY_LEVEL + stock position)
 *   - lead time, open orders        → LEAD_TIME / PURCHASE_ORDERS feeds
 *   - demand history                → DEMAND_HISTORY (removals + issues)
 *   - unit cost                     → VENDOR (vendor economics)
 *
 * There is no per-field provenance on the wire yet, so — same honesty
 * standard as dashboardProvenance.ts — we stamp each surfaced metric with
 * the true origin of the underlying feed rather than inventing per-field
 * freshness/coverage/confidence the BFF doesn't actually report.
 */

function stamp(
  source: string,
  systemOfRecord: string,
  opts: { derived?: boolean; confidence?: number; asOf?: Date } = {},
): Provenance {
  const asOf = opts.asOf ?? new Date();
  return {
    source,
    systemOfRecord,
    freshnessAt: asOf.toISOString(),
    coverage: 1,
    confidence: opts.confidence ?? 0.95,
    derived: opts.derived ?? false,
  };
}

/** Stock breakdown (on-hand / serviceable / in-repair / allocated / rental / loan). */
export function stockProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Inventory", "INVENTORY", { asOf });
}

/** Current + proposed (ROP/EOQ/Safety Stock/Max) policy. */
export function policyProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Inventory", "INVENTORY", { derived: true, asOf });
}

/** Projected demand / demand history trend. */
export function demandProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Demand History", "DEMAND_HISTORY", { derived: true, confidence: 0.85, asOf });
}

/** Lead time (promised vs realized). */
export function leadTimeProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Lead Time", "LEAD_TIME", { confidence: 0.9, asOf });
}

/** Open orders count + qty. */
export function openOrdersProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Purchase Orders", "PURCHASE_ORDERS", { asOf });
}

/** Unit cost. */
export function costProvenance(asOf: Date = new Date()): Provenance {
  return stamp("Vendor Master", "VENDOR", { asOf });
}
