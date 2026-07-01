import type { FeedHealthRow } from "@/lib/api/types";
import { RECOMMENDED_FEED_RANK, RECOMMENDED_FEED_WHY } from "@/features/feeds/recommendedFeedsCopy";

export interface RecommendedFeedsProps {
  rows: FeedHealthRow[];
}

/**
 * PRD §6.7 — "Recommended feeds to add, ranked by impact on optimization quality."
 * Scoped to the real not_connected feeds only (a connected/partial feed is never a
 * "recommendation to add"), ordered by RECOMMENDED_FEED_RANK — RELIABILITY first,
 * per the PRD's own risk table naming reliability/MTBUR enrichment "the #1
 * recommended feed" (PRD §10).
 */
export function RecommendedFeeds({ rows }: RecommendedFeedsProps) {
  const notConnected = rows.filter((row) => row.status === "not_connected");
  const rank = new Map(RECOMMENDED_FEED_RANK.map((id, i) => [id, i]));
  const ranked = [...notConnected].sort(
    (a, b) => (rank.get(a.feed_id) ?? 99) - (rank.get(b.feed_id) ?? 99),
  );

  if (ranked.length === 0) {
    return <p className="text-sm text-ink-2">All 13 feeds are connected or partially connected.</p>;
  }

  return (
    <ol className="flex flex-col gap-3">
      {ranked.map((row, index) => (
        <li key={row.feed_id} className="flex items-start gap-3 rounded-md border border-line p-3">
          <span
            className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-panel-2 text-xs font-semibold text-ink-2"
            aria-hidden="true"
          >
            {index + 1}
          </span>
          <div>
            <div className="text-sm font-medium text-ink">{row.name}</div>
            <p className="text-xs text-ink-2">
              {RECOMMENDED_FEED_WHY[row.feed_id] ?? row.notes}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
