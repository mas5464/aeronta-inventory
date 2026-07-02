import { Metric } from "@/components/Metric";
import { withProvenance } from "@/lib/provenance";
import { scenarioProvenance } from "@/lib/scenarioProvenance";
import type { ScenarioSolveResult } from "@/lib/api/types";

export interface ScenarioOutcomePanelProps {
  result: ScenarioSolveResult;
}

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const currencyDeltaFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
  signDisplay: "always",
});

const pctFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

const pctDeltaFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
  signDisplay: "always",
});

/**
 * Projected outcome vs. current plan (PRD §6.5): investment, coverage, the deltas,
 * and an honest skipped-keys disclosure — every number flows through Metric/ProvChip
 * per the provenance invariant (docs/DESIGN-SYSTEM.md §4).
 */
export function ScenarioOutcomePanel({ result }: ScenarioOutcomePanelProps) {
  const provenance = scenarioProvenance();
  const { current, proposed, delta_investment, delta_coverage, skipped_keys, total_keys } =
    result;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <span className="text-xs text-ink-2">Current plan</span>
          <span className="text-sm text-ink">
            {pctFormatter.format(current.service_level)} service level
          </span>
          <span className="text-sm tabular-nums text-ink-2">
            {currencyFormatter.format(current.projected_investment)} investment ·{" "}
            {pctFormatter.format(current.projected_coverage)} coverage
          </span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs text-ink-2">Proposed scenario</span>
          <span className="text-sm font-medium text-ink">
            {pctFormatter.format(proposed.service_level)} service level
          </span>
          <span className="text-sm tabular-nums text-ink-2">
            {currencyFormatter.format(proposed.projected_investment)} investment ·{" "}
            {pctFormatter.format(proposed.projected_coverage)} coverage
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <Metric
          label="Projected investment"
          metric={withProvenance(proposed.projected_investment, provenance)}
          format={currencyFormatter.format}
        />
        <Metric
          label="Investment delta vs. plan"
          metric={withProvenance(delta_investment, provenance)}
          format={currencyDeltaFormatter.format}
        />
        <Metric
          label="Coverage delta vs. plan"
          metric={withProvenance(delta_coverage, provenance)}
          format={pctDeltaFormatter.format}
        />
        <Metric
          label="Parts already at target (on-hand)"
          metric={withProvenance(proposed.on_hand_gap_ratio, provenance)}
          format={pctFormatter.format}
        />
      </div>
      <p className="text-xs text-ink-2">
        "Parts already at target" is how much of today's real on-hand already meets the
        proposed policy — the buying gap to close, not a service-level figure. It is not
        expected to move in the same direction as the service-level slider.
      </p>
      <p className="text-xs text-ink-2">
        Interactive approximation — uniform (R,Q) model across all demand regimes.
      </p>

      {result.budget_cap_binds && (
        <div role="alert" className="rounded-md border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          The proposed scenario exceeds the budget cap — projected investment (
          {currencyFormatter.format(proposed.projected_investment)}) is above the cap.
        </div>
      )}

      {skipped_keys > 0 && (
        <p role="status" className="text-xs text-ink-2">
          {skipped_keys.toLocaleString()} of {total_keys.toLocaleString()} parts network-wide
          could not be scored — missing demand history, criticality, vendor cost, or stock
          position — and are excluded from every projection here rather than defaulted. This is
          a data-quality gap, separate from the scenario's own scope filter (
          {proposed.scored_keys.toLocaleString()} parts scored in the current scope).
        </p>
      )}
    </div>
  );
}
