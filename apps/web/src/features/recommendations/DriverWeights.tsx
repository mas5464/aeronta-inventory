import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RecommendationDetail } from "@/lib/api/types";

export interface DriverWeightsProps {
  details: RecommendationDetail[];
}

/**
 * "How the optimizer decides" (PRD §6.3). The BFF does not expose driver
 * *weights* on the wire (no `/recommendations/factors`-style route) — the
 * closest real signal is each recommendation's `supporting_evidence` kinds
 * and `guardrail_flags`. We present the evidence-kind frequency across the
 * loaded detail set as a proxy for "what the optimizer looked at," and call
 * out explicitly that these are evidence counts, not calibrated weights.
 */
export function DriverWeights({ details }: DriverWeightsProps) {
  const counts = new Map<string, number>();
  for (const detail of details) {
    for (const ev of detail.supporting_evidence) {
      counts.set(ev.kind, (counts.get(ev.kind) ?? 0) + 1);
    }
  }
  const total = Array.from(counts.values()).reduce((a, b) => a + b, 0);
  const entries = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>How the optimizer decides</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-xs text-ink-3">
          Driver weights are not exposed by the BFF yet. Shown below is the relative frequency of
          supporting-evidence kinds across the loaded recommendations, as a proxy for what the
          optimizer drew on — not a calibrated weight breakdown.
        </p>
        {entries.length === 0 ? (
          <p className="text-sm text-ink-2">No supporting evidence loaded yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {entries.map(([kind, count]) => {
              const pct = total === 0 ? 0 : Math.round((count / total) * 100);
              return (
                <li key={kind} className="flex items-center gap-3">
                  <span className="w-40 shrink-0 text-sm text-ink-2">{kind}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-panel-2">
                    <div className="h-full rounded-full bg-brand" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="w-10 text-right text-xs tabular-nums text-ink-2">{pct}%</span>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
