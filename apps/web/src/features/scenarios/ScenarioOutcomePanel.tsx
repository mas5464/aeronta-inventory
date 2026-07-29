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

const unitsFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 1,
});

/**
 * Projected outcome vs. current plan (PRD §6.5): investment, coverage, the deltas,
 * and an honest skipped-keys disclosure — every number flows through Metric/ProvChip
 * per the provenance invariant (docs/DESIGN-SYSTEM.md §4).
 */
export function ScenarioOutcomePanel({ result }: ScenarioOutcomePanelProps) {
  const provenance = scenarioProvenance(result);
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

      {result.repair_current && result.repair_proposed && (
        <section
          role="region"
          aria-label="Repair return scenario outcome"
          className="rounded-card border border-line bg-panel-2 p-3"
        >
          <h3 className="text-sm font-semibold text-ink">
            Repair returns within{" "}
            {result.repair_proposed.horizon_days.toLocaleString()} days
          </h3>
          <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div>
              <dt className="text-xs text-ink-2">Current expected</dt>
              <dd className="mt-1 text-lg font-semibold text-ink">
                {unitsFormatter.format(result.repair_current.expected_units)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-2">Proposed expected</dt>
              <dd className="mt-1 text-lg font-semibold text-ink">
                {unitsFormatter.format(result.repair_proposed.expected_units)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-2">Eligible open WIP</dt>
              <dd className="mt-1 text-lg font-semibold text-ink">
                {result.repair_proposed.eligible_quantity.toLocaleString()}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-2">Modeled keys</dt>
              <dd className="mt-1 text-lg font-semibold text-ink">
                {result.repair_proposed.modeled_keys.toLocaleString()}
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-ink-2">
            Repair estimates use a{" "}
            {pctFormatter.format(
              result.repair_proposed.serviceable_yield_assumption,
            )}{" "}
            serviceable-yield model assumption, not an observed portfolio
            yield. {result.repair_proposed.unavailable_keys.toLocaleString()}{" "}
            eligible keys lacked usable REP evidence and receive zero projected
            return credit.{" "}
            {(result.repair_proposed.unscoped_keys ?? 0).toLocaleString()}{" "}
            eligible repair keys lacked the selected scope metadata and were
            disclosed rather than treated as non-matches.
          </p>
          <p className="mt-2 text-xs text-ink-2">
            Procurement lead changes do not alter this repair estimate; repair
            TAT changes do not alter procurement policy math.
          </p>
        </section>
      )}

      {result.assumption_impacts && result.assumption_impacts.length > 0 && (
        <section
          aria-label="Changed scenario assumptions"
          className="rounded-card border border-line p-3"
        >
          <h3 className="text-sm font-semibold text-ink">
            Changed assumptions
          </h3>
          <ul className="mt-2 flex flex-col gap-1 text-sm text-ink-2">
            {result.assumption_impacts.map((impact) => (
              <li key={impact.label}>
                {impact.label} ·{" "}
                {impact.affected_key_count.toLocaleString()} affected keys
              </li>
            ))}
          </ul>
        </section>
      )}

      {result.fingerprint && (
        <dl
          className="grid gap-2 border-t border-line pt-3 text-xs sm:grid-cols-[10rem_1fr]"
          aria-label="Scenario result identity"
        >
          <dt className="text-ink-3">Contract version</dt>
          <dd className="text-ink-2">
            {result.contract_version ?? "scenario-solve.v1"}
          </dd>
          <dt className="text-ink-3">Affected keys</dt>
          <dd className="text-ink-2">
            {result.affected_key_count?.toLocaleString() ?? "Unavailable"}
          </dd>
          <dt className="text-ink-3">Fingerprint</dt>
          <dd
            className="break-all font-mono text-ink-2"
            data-testid="scenario-fingerprint"
          >
            {result.fingerprint}
          </dd>
        </dl>
      )}

      {result.warning_codes && result.warning_codes.length > 0 && (
        <section
          aria-label="Scenario warnings"
          className="rounded-card border border-warn/40 bg-warn/10 p-3"
        >
          <h3 className="text-sm font-semibold text-ink">Scenario warnings</h3>
          <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-xs text-ink-2">
            {result.warning_codes.map((warning) => (
              <li key={warning}>
                <code className="font-mono text-ink">{warning}</code>
              </li>
            ))}
          </ul>
        </section>
      )}

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
