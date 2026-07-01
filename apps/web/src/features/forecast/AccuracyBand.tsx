import { ProvChip } from "@/components/ProvChip";
import type { ForecastAccuracy } from "@/lib/api/types";
import type { Provenance } from "@/lib/provenance";

export interface AccuracyBandProps {
  accuracy: ForecastAccuracy;
  /** Provenance for the proxy points — DEMAND_HISTORY, stamped with reduced confidence
   * to reflect that this is a derived proxy, not a backtest. */
  provenance: Provenance;
}

const integerFormatter = new Intl.NumberFormat("en-US");

/**
 * PRD §6.6 — "Network actual-vs-forecast with confidence band." No backtest runs at
 * serve time (see docs on `bff/store.py::forecast_summary`), so this deliberately does
 * NOT render a MAPE/bias metric or a P50/P90 band — that would be fabricated. Instead
 * it always shows the honest-gap banner plus the one truthful proxy the BFF exposes:
 * recent real DEMAND_HISTORY actuals vs. the engine's current per-day projection,
 * scaled to the same period and summed across the portfolio.
 */
export function AccuracyBand({ accuracy, provenance }: AccuracyBandProps) {
  return (
    <div className="flex flex-col gap-3">
      <div
        role="status"
        className="rounded-md border border-warn/40 bg-warn/10 p-3 text-xs text-warn"
      >
        Forecast accuracy is not yet connected — {accuracy.note}
      </div>

      {accuracy.points.length === 0 ? (
        <p className="text-sm text-ink-2">No recent demand-history periods available.</p>
      ) : (
        <>
          <ProvChip provenance={provenance} />
          <table className="w-full text-sm">
            <caption className="sr-only">
              Recent actual vs. projected demand, network-aggregated (proxy)
            </caption>
            <thead>
              <tr className="border-b border-line text-left text-xs text-ink-2">
                <th scope="col" className="pb-2 pr-3 font-medium">
                  Period
                </th>
                <th scope="col" className="pb-2 pr-3 font-medium">
                  Actual
                </th>
                <th scope="col" className="pb-2 font-medium">
                  Projected
                </th>
              </tr>
            </thead>
            <tbody>
              {accuracy.points.map((point) => (
                <tr key={point.period_start} className="border-b border-line/60 last:border-0">
                  <td className="py-2 pr-3 font-medium text-ink">{point.period_start}</td>
                  <td className="py-2 pr-3 tabular-nums text-ink">
                    {integerFormatter.format(Math.round(point.actual))}
                  </td>
                  <td className="py-2 tabular-nums text-ink-2">
                    {integerFormatter.format(Math.round(point.projected))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
