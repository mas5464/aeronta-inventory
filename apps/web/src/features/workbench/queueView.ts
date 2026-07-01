import type { AogRiskLevel, AutonomyTier, QueueRow, RecommendationType } from "@/lib/api/types";

/** Pill filter state for the Workbench worklist (client-side over the loaded page). */
export interface QueueFilters {
  tier: AutonomyTier | "all";
  type: RecommendationType | "all";
  aogOnly: boolean;
}

export const DEFAULT_QUEUE_FILTERS: QueueFilters = {
  tier: "all",
  type: "all",
  aogOnly: false,
};

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

/**
 * Applies the pill filters (tier / type / AOG) to a loaded page of rows.
 * This runs client-side over the currently-loaded page — the BFF's queue
 * route (`GET …/recommendations`) does not accept tier/type/AOG query
 * params server-side yet (see app.py's `queue()` docstring comment), so
 * this is a documented client-side narrowing of the fetched page, not a
 * full-dataset filter.
 */
export function applyQueueFilters(rows: QueueRow[], filters: QueueFilters): QueueRow[] {
  return rows.filter((row) => {
    if (filters.tier !== "all" && row.tier !== filters.tier) return false;
    if (filters.type !== "all" && row.type !== filters.type) return false;
    if (filters.aogOnly && row.aog_risk_level < 3) return false;
    return true;
  });
}

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
