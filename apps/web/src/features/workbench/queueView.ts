import type { AogRiskLevel, AutonomyTier, QueueRow, RecommendationType } from "@/lib/api/types";

/**
 * Large-table strategy (Slice S8 hardening): the Workbench worklist does NOT
 * virtualize rows. At full scale (~40k SKUs network-wide) the strategy is
 * **server-side pagination**, not client-side virtualization — the BFF's
 * `GET …/recommendations?limit=&offset=` already pages the query
 * (`store.py::queue()`), so the browser only ever renders one page's worth
 * of `<tr>`s (`PAGE_SIZE`, well under `MAX_PAGE_SIZE`) regardless of how
 * many total recommendations exist network-wide. A plain `<table>` of ≤200
 * rows renders instantly in every modern browser, so a virtualization
 * library (react-window/react-virtual) would add a dependency + complexity
 * for a problem pagination already solves. The tier/type/AOG pills (task F4)
 * are now server-side sort/filter params (see workbenchQueryState.ts) — they
 * narrow what the BFF returns, not a client-side pass over the loaded page,
 * so they do not defeat this bound either way.
 */
export const MAX_PAGE_SIZE = 200;

export const RECOMMENDATION_TYPE_LABEL: Record<RecommendationType, string> = {
  purchase: "Purchase",
  transfer: "Transfer",
  reduce_stock: "Reduce stock",
  sell: "Sell",
  adjust_min_max: "Adjust min/max",
};

export const TIER_LABEL: Record<AutonomyTier, string> = {
  1: "Tier A · Advisor",
  2: "Tier B · Bounded",
  3: "Tier C · Autonomous",
};

export const AOG_RISK_LABEL: Record<AogRiskLevel, string> = {
  0: "None",
  1: "Low",
  2: "Medium",
  3: "High",
  4: "Critical",
};

/** High-confidence threshold used by the client-side confidence filter + bulk-accept preview. */
export const HIGH_CONFIDENCE_THRESHOLD = 0.8;

/**
 * Confidence is not a field on `BulkApproveFilter` (the BFF's bulk-approve
 * body only takes tiers / max_delta_pct / criticality_min / types), so
 * "bulk accept high-confidence" is implemented as: filter the *loaded page*
 * client-side by confidence, then bulk-approve by the tiers/criticality
 * actually present among the matching rows. Documented here + in the
 * Workbench UI copy — do not silently invent a fake server-side capability.
 */
export function highConfidenceRows(rows: QueueRow[], threshold: number = HIGH_CONFIDENCE_THRESHOLD): QueueRow[] {
  return rows.filter((row) => row.confidence_score >= threshold && row.approvable);
}
