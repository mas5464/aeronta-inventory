import type { Breakdown } from "@/lib/api/types";

export interface SlInvestmentPanelProps {
  /** `by_criticality` breakdown — used as an honest coverage-vs-shortage proxy. */
  byCriticality: Breakdown[];
  labelFor?: (key: string) => string;
}

const integerFormatter = new Intl.NumberFormat("en-US");

/**
 * PRD §6.1 calls for a "service-level-vs-inventory-investment" dual-axis
 * trend. The BFF's `DashboardSummary` does not expose a service-level
 * series (no fill-rate/target field, no time series) — only point-in-time
 * `by_criticality` breakdowns of count/on_hand/shortage. Rather than
 * fabricate a service-level number, this renders the one truthful proxy we
 * can derive — on-hand coverage (on_hand vs shortage) per criticality band —
 * with an explicit, honest banner that real service-level-vs-investment
 * data isn't wired yet.
 */
export function SlInvestmentPanel({ byCriticality, labelFor }: SlInvestmentPanelProps) {
  const hasData = byCriticality.length > 0;

  return (
    <div className="flex flex-col gap-3">
      <div
        role="status"
        className="rounded-md border border-warn/40 bg-warn/10 p-3 text-xs text-warn"
      >
        Service-level-vs-investment trend is not yet connected — the BFF does not expose a
        service-level (fill-rate) series. Shown below is on-hand coverage by criticality band,
        the closest truthful proxy available today from real eMRO extract data.
      </div>

      {!hasData ? (
        <p className="text-sm text-ink-2">No criticality breakdown available.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {byCriticality.map((band) => {
            const denom = band.on_hand + band.shortage;
            const coveragePct = denom === 0 ? 100 : Math.round((band.on_hand / denom) * 100);
            return (
              <li key={band.key} className="flex items-center gap-3 text-sm">
                <span className="w-24 shrink-0 font-medium text-ink">
                  {labelFor ? labelFor(band.key) : band.key}
                </span>
                <span
                  className="h-2 flex-1 rounded-full bg-panel-2"
                  role="img"
                  aria-label={`${labelFor ? labelFor(band.key) : band.key}: ${coveragePct}% on-hand coverage, ${integerFormatter.format(band.count)} parts`}
                >
                  <span
                    className="block h-2 rounded-full bg-good"
                    style={{ width: `${coveragePct}%` }}
                  />
                </span>
                <span className="w-16 shrink-0 text-right tabular-nums text-ink-2">
                  {coveragePct}%
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
