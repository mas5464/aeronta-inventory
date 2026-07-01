import type { FeedConnectionStatus, FeedHealthRow } from "@/lib/api/types";

/** Status filter state for the 13-feed table (PRD §6.7 "filterable"). */
export type FeedStatusFilter = FeedConnectionStatus | "all";

export const FEED_STATUS_LABEL: Record<FeedConnectionStatus, string> = {
  connected: "Connected",
  partial: "Partial",
  not_connected: "Not connected",
};

/** Filters the 13-row feed table by connection status. Client-side over one small,
 * fully-loaded 13-row payload — no pagination or server-side filtering needed. */
export function filterFeeds(rows: FeedHealthRow[], filter: FeedStatusFilter): FeedHealthRow[] {
  if (filter === "all") return rows;
  return rows.filter((row) => row.status === filter);
}
