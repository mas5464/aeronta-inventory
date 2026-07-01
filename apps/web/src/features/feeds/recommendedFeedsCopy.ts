import type { FeedId } from "@/lib/api/types";

/**
 * "Recommended feeds to add" why-it-matters copy (PRD §6.7), keyed by FeedId. Only
 * covers the not_connected feeds — a connected/partial feed never needs a
 * "why add this" pitch. Kept intentionally short (one sentence).
 *
 * RELIABILITY is ranked #1: the PRD's own risk table (§10) names reliability/MTBUR
 * enrichment as "the #1 recommended feed" because thin coverage (~79%) weakens every
 * rotable float calculation network-wide.
 */
export const RECOMMENDED_FEED_RANK: FeedId[] = [
  "RELIABILITY",
  "MAINTENANCE_SCHEDULE",
  "REPAIR_ORDERS",
  "SERIAL_TRACKING",
  "QUOTATIONS",
  "CONTRACTS",
];

export const RECOMMENDED_FEED_WHY: Partial<Record<FeedId, string>> = {
  RELIABILITY: "Thin MTBUR/MTBF coverage weakens every rotable float calculation network-wide.",
  MAINTENANCE_SCHEDULE:
    "Forward-looking check schedules would sharpen scheduled-demand projections beyond today's stub.",
  REPAIR_ORDERS: "Real shop-floor TAT would replace the engine's zero-value repair-TAT stub.",
  SERIAL_TRACKING: "Per-unit rotable status/location would enable true serial-level float planning.",
  QUOTATIONS: "Live RFQ pricing would sharpen buy-vs-repair cost comparisons.",
  CONTRACTS: "PBH/pooling terms would correct cost and availability assumptions for pooled parts.",
};
