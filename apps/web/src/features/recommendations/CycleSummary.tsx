import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RECOMMENDATION_TYPE_LABEL } from "@/features/workbench/queueView";
import type { QueueRow, RecommendationType } from "@/lib/api/types";

const integerFormatter = new Intl.NumberFormat("en-US");

export interface CycleSummaryProps {
  rows: QueueRow[];
}

/**
 * Cycle summary (PRD §6.3): counts by type/AOG, derived from the loaded
 * queue page (the BFF has no dedicated `/recommendations/summary` route —
 * this is computed client-side from the same `QueueRow[]` the Workbench
 * renders).
 */
export function CycleSummary({ rows }: CycleSummaryProps) {
  const byType = new Map<RecommendationType, number>();
  let aogCount = 0;
  for (const row of rows) {
    byType.set(row.type, (byType.get(row.type) ?? 0) + 1);
    if (row.aog_risk_level >= 3) aogCount += 1;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cycle summary</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-6">
        <div className="flex flex-col gap-1">
          <span className="text-xs text-ink-2">Total recommendations</span>
          <b className="text-2xl font-semibold text-ink">{integerFormatter.format(rows.length)}</b>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs text-ink-2">AOG risk (high/critical)</span>
          <b className="text-2xl font-semibold text-bad">{integerFormatter.format(aogCount)}</b>
        </div>
        {Array.from(byType.entries()).map(([type, count]) => (
          <div key={type} className="flex flex-col gap-1">
            <span className="text-xs text-ink-2">{RECOMMENDATION_TYPE_LABEL[type]}</span>
            <b className="text-2xl font-semibold text-ink">{integerFormatter.format(count)}</b>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
