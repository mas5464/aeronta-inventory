import type { MethodCoverageRow } from "@/lib/api/types";

export interface MethodCoverageBarsProps {
  rows: MethodCoverageRow[];
  totalSkus: number;
}

const integerFormatter = new Intl.NumberFormat("en-US");

/**
 * PRD §6.6 — "Forecast-method coverage (ML, Croston/SBA, MTBUR-based, moving
 * average, manual override)." Each row's `method` is the forecast method the engine
 * actually serves for that regime in v1 (see store.py's `_REGIME_METHOD`), and
 * `sku_count`/`pct` come from running the real deterministic regime classifier over
 * every (PN, Location) key's actual DEMAND_HISTORY — not a sample.
 */
export function MethodCoverageBars({ rows, totalSkus }: MethodCoverageBarsProps) {
  if (rows.length === 0) {
    return <p className="text-sm text-ink-2">No method-coverage data available.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-ink-2">
        {integerFormatter.format(totalSkus)} SKUs classified by demand regime.
      </p>
      <ul className="flex flex-col gap-2" aria-label="Forecast-method coverage">
        {rows.map((row) => {
          const pct = Math.round(row.pct * 100);
          return (
            <li key={row.regime} className="flex items-center gap-3 text-sm">
              <span className="w-40 shrink-0 font-medium text-ink">{row.method}</span>
              <span
                className="h-2 flex-1 rounded-full bg-panel-2"
                role="img"
                aria-label={`${row.method}: ${pct}% of SKUs, ${integerFormatter.format(row.sku_count)} parts, regime ${row.regime}`}
              >
                <span className="block h-2 rounded-full bg-series-1" style={{ width: `${pct}%` }} />
              </span>
              <span className="w-24 shrink-0 text-right tabular-nums text-ink-2">
                {integerFormatter.format(row.sku_count)} ({pct}%)
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
