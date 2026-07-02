import { TIER_LABEL } from "@/features/workbench/queueView";
import type { AutonomyTier } from "@/lib/api/types";

/** Which drill-content renderer a `DrillSpec` maps to (dispatched by `DrillContent`). */
export type DrillKind = "breakdown" | "shortages";

export interface DrillSpec {
  /** Stable identifier — used as the panel `id` suffix and `KPI_DRILL_MAP` target. */
  id: string;
  /** Panel/region title (also the `DrillPanel`'s accessible name). */
  title: string;
  kind: DrillKind;
  /** Required when `kind === "breakdown"` — which `DashboardSummary` breakdown to render. */
  breakdownKey?: "by_criticality" | "by_ata" | "by_part_class" | "by_tier";
  /** Optional per-row label mapper (e.g. "Tier 1" instead of the raw key "1"). */
  labelFor?: (key: string) => string;
  /** Muted one-line explanation shown above the table — what this panel covers. */
  description: string;
}

function criticalityLabel(key: string): string {
  return `Tier ${key}`;
}

/** `by_tier`'s `key` is a stringified `AutonomyTier` (1/2/3) — reuse the Workbench's real labels. */
function autonomyTierLabel(key: string): string {
  const tier = Number(key) as AutonomyTier;
  return TIER_LABEL[tier] ?? key;
}

/**
 * The registry of every drill panel Overview can open. Each of the 4
 * `Breakdown[]` arrays on `DashboardSummary` (by_criticality, by_ata,
 * by_part_class, by_tier) is covered by at least one spec here — including
 * by_part_class and by_tier, which the BFF computes but which, before this
 * slice, were rendered nowhere in the UI (see `Overview.tsx`'s history:
 * only by_criticality and by_ata ever reached a component). See the
 * "drill spec completeness" test below for the regression guard.
 */
export const DRILL_SPECS: readonly DrillSpec[] = [
  {
    id: "health-mix",
    title: "Inventory health mix — full breakdown",
    kind: "breakdown",
    breakdownKey: "by_criticality",
    labelFor: criticalityLabel,
    description: "Every criticality tier behind the health-mix donut, not just its chart slices.",
  },
  {
    id: "sl-investment",
    title: "Service level vs. investment — full breakdown",
    kind: "breakdown",
    breakdownKey: "by_criticality",
    labelFor: criticalityLabel,
    description:
      "The same by-criticality coverage proxy behind the SL-vs-investment panel, as a full table.",
  },
  {
    id: "ata-risk",
    title: "Risk by ATA chapter — full list",
    kind: "breakdown",
    breakdownKey: "by_ata",
    description: "Every ATA chapter with shortage risk, not just the top 8 shown in the ranked list.",
  },
  {
    id: "priority-actions",
    title: "Priority actions — full list",
    kind: "shortages",
    description: "All top shortages driving priority actions, not just the top 5 previewed on the card.",
  },
  {
    id: "by-part-class",
    title: "Breakdown by part class",
    kind: "breakdown",
    breakdownKey: "by_part_class",
    description: "Parts, on-hand, and shortage grouped by part class — computed by the BFF, shown here.",
  },
  {
    id: "by-tier",
    title: "Breakdown by autonomy tier",
    kind: "breakdown",
    breakdownKey: "by_tier",
    labelFor: autonomyTierLabel,
    description:
      "Parts, on-hand, and shortage grouped by autonomy tier (A/B/C) — computed by the BFF, shown here.",
  },
] as const;

/** Maps an Overview KPI card's key to the `DrillSpec.id` it opens. */
export const KPI_DRILL_MAP: Record<string, string> = {
  parts: "by-part-class",
  total_on_hand: "health-mix",
  on_hand_value: "by-part-class",
  total_shortage: "ata-risk",
  projected_demand: "health-mix",
  aog_exposure: "by-tier",
  open_recommendations: "by-tier",
  net_cost_impact: "priority-actions",
};
