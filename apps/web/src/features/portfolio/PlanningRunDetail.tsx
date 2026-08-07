import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import {
  usePlanningRunSelections,
} from "@/lib/api/usePlanningRuns";
import type {
  PlanningChoiceSnapshot,
  PlanningObjectiveWeights,
  PlanningRejectedAlternative,
  PlanningRunSelectionRecord,
  PlanningRunView,
  SolverEvidenceWire,
} from "@/lib/api/planningRuns";
import {
  formatPlanningDate,
  formatPlanningMoney,
  formatPlanningNumber,
  formatPlanningPercent,
  planningStatusLabel,
} from "@/features/portfolio/portfolioView";

function MetricTile({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div className="rounded-control border border-line p-3">
      <p className="text-lg font-semibold tabular-nums text-ink">{value}</p>
      <p className="text-xs text-ink-2">{label}</p>
      {note && <p className="mt-1 text-xs text-ink-3">{note}</p>}
    </div>
  );
}

function SolverEvidence({ solver }: { solver: SolverEvidenceWire }) {
  const feasible = solver.termination === "optimal" || solver.termination === "not_proven";
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Solver evidence</CardTitle>
          <Badge
            variant={
              solver.termination === "optimal"
                ? "good"
                : solver.termination === "not_proven"
                  ? "warn"
                  : "bad"
            }
          >
            {solver.termination === "optimal"
              ? "Optimality proven"
              : solver.termination === "not_proven"
                ? "Feasible · not proven optimal"
                : planningStatusLabel(
                    solver.termination === "infeasible"
                      ? "infeasible"
                      : "failed",
                  )}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs md:grid-cols-4">
          <div>
            <dt className="text-ink-3">Objective</dt>
            <dd className="font-medium tabular-nums text-ink">
              {formatPlanningNumber(solver.objective, 4)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Best bound</dt>
            <dd className="font-medium tabular-nums text-ink">
              {formatPlanningNumber(solver.objective_bound, 4)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Relative gap</dt>
            <dd className="font-medium tabular-nums text-ink">
              {formatPlanningPercent(solver.relative_gap, 2)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Solve duration</dt>
            <dd className="font-medium tabular-nums text-ink">
              {formatPlanningNumber(solver.duration_ms, 0)} ms
            </dd>
          </div>
        </dl>
        <p className="text-xs text-ink-2">{solver.message}</p>
        {feasible && !solver.optimality_proven && (
          <p role="status" className="text-xs text-warn">
            This is the best feasible result found within the approved time
            limit. The displayed gap is bounded solver evidence, not a claim
            of optimality.
          </p>
        )}
        <p className="text-xs text-ink-3">
          {solver.implementation} {solver.implementation_version} ·{" "}
          {solver.optimizer_version}
          {solver.node_count !== null
            ? ` · ${solver.node_count.toLocaleString("en-US")} nodes`
            : ""}
        </p>
      </CardContent>
    </Card>
  );
}

function ObjectiveWeights({
  weights,
}: {
  weights: PlanningObjectiveWeights | undefined;
}) {
  if (!weights) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Objective definition</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-3 text-xs lg:grid-cols-4">
          <div>
            <dt className="text-ink-3">Shortage reduction</dt>
            <dd className="font-medium text-ink">
              {formatPlanningNumber(weights.shortage_reduction_weight, 4)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">AOG risk reduction</dt>
            <dd className="font-medium text-ink">
              {formatPlanningNumber(weights.aog_risk_reduction_weight, 4)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Holding penalty</dt>
            <dd className="font-medium text-ink">
              {formatPlanningNumber(weights.holding_cost_penalty_weight, 4)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Ordering penalty</dt>
            <dd className="font-medium text-ink">
              {formatPlanningNumber(weights.ordering_cost_penalty_weight, 4)}
            </dd>
          </div>
        </dl>
        <p className="mt-3 text-xs text-ink-3">
          Criticality multipliers:{" "}
          {Object.entries(weights.criticality_weights)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([tier, weight]) => `Tier ${tier} ${weight}×`)
            .join(" · ")}
        </p>
      </CardContent>
    </Card>
  );
}

function CoverageDisclosure({ run }: { run: PlanningRunView }) {
  const coverage = run.coverage;
  if (!coverage) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Planning data coverage</CardTitle>
        </CardHeader>
        <CardContent>
          <p role="status" className="text-sm text-warn">
            Coverage evidence is unavailable for this legacy run. No complete
            repair-credit or TAT-confidence claim can be made.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Planning data coverage</CardTitle>
          <Badge
            variant={
              coverage.tat_confidence_status === "available"
                ? "good"
                : coverage.tat_confidence_status === "partial"
                  ? "warn"
                  : "default"
            }
          >
            Repair TAT evidence {coverage.tat_confidence_status}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <dl className="grid grid-cols-2 gap-3 text-xs lg:grid-cols-4">
          <div>
            <dt className="text-ink-3">Candidate-menu coverage</dt>
            <dd className="font-medium text-ink">
              {formatPlanningPercent(coverage.candidate_menu_coverage_rate)}
            </dd>
            <dd className="text-ink-3">
              {coverage.eligible_key_count}/{coverage.authoritative_key_count} keys
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Repair-model coverage</dt>
            <dd className="font-medium text-ink">
              {formatPlanningPercent(coverage.repair_model_coverage_rate)}
            </dd>
            <dd className="text-ink-3">
              {coverage.repair_model_key_count}/{coverage.scope_key_count} keys
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Explicit repair-credit coverage</dt>
            <dd className="font-medium text-ink">
              {formatPlanningPercent(coverage.repair_credit_coverage_rate)}
            </dd>
            <dd className="text-ink-3">
              {coverage.repair_credit_key_count}/{coverage.scope_key_count} keys
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Minimum candidate confidence</dt>
            <dd className="font-medium text-ink">
              {formatPlanningPercent(coverage.minimum_candidate_confidence)}
            </dd>
            <dd className="text-ink-3">
              {coverage.low_confidence_key_count.toLocaleString("en-US")} low-confidence keys
            </dd>
          </div>
        </dl>
        <p className="text-xs text-ink-3">{coverage.disclosure}</p>
        {coverage.missing_candidate_frontier_key_count > 0 && (
          <p role="status" className="text-xs text-warn">
            {coverage.missing_candidate_frontier_key_count.toLocaleString(
              "en-US",
            )}{" "}
            authoritative keys were excluded because no immutable candidate
            frontier was available. They are counted as skipped, not silently
            removed from the denominator.
          </p>
        )}
        {coverage.criticality_unknown_key_count > 0 && (
          <p role="status" className="text-xs text-warn">
            {coverage.criticality_unknown_key_count.toLocaleString("en-US")}{" "}
            authoritative keys lack a known criticality tier.
          </p>
        )}
        {(coverage.tat_confidence_status !== "available" ||
          coverage.repair_credit_coverage_rate !== "1") && (
          <p role="status" className="text-xs text-warn">
            Repair-aware outputs are limited to the explicit evidence above;
            unavailable coverage is not estimated or filled with false
            precision.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function constraintLabels(choice: PlanningChoiceSnapshot): string[] {
  return Array.from(
    new Set([
      ...choice.hard_constraint_ids.map((id) => `Hard · ${id}`),
      ...choice.mandatory_floor_ids.map((id) => `Floor · ${id}`),
      ...choice.infeasibility_reasons.map(
        (reason) => `Infeasible · ${reason}`,
      ),
    ]),
  );
}

function ChoiceComparisonRow({
  state,
  choice,
  currency,
  reason,
  selected = false,
}: {
  state: string;
  choice: PlanningChoiceSnapshot;
  currency: string;
  reason?: string;
  selected?: boolean;
}) {
  const constraints = constraintLabels(choice);
  return (
    <tr className={selected ? "bg-brand/5" : undefined}>
      <th scope="row" className="min-w-48 px-3 py-3 align-top">
        <span className="block text-[0.68rem] font-semibold uppercase tracking-wide text-ink-3">
          {state}
        </span>
        <span className="mt-1 block font-medium text-ink">{choice.label}</span>
        <span className="mt-1 block font-normal text-ink-3">
          {choice.candidate_kind.replace(/_/g, " ")}
        </span>
        {reason && (
          <span className="mt-2 block max-w-xl font-normal leading-relaxed text-ink-2">
            {reason}
          </span>
        )}
      </th>
      <td className="px-3 py-3 text-right tabular-nums text-ink">
        {formatPlanningMoney(choice.acquisition_cash, currency)}
      </td>
      <td className="px-3 py-3 text-right tabular-nums text-ink">
        {formatPlanningPercent(choice.expected_service_level, 1)}
      </td>
      <td className="px-3 py-3 text-right tabular-nums text-ink">
        {formatPlanningNumber(choice.expected_shortage, 3)}
      </td>
      <td className="px-3 py-3 text-right tabular-nums text-ink">
        {formatPlanningPercent(choice.expected_aog_risk, 1)}
      </td>
      <td className="px-3 py-3 text-right font-medium tabular-nums text-ink">
        {formatPlanningNumber(choice.objective.total, 3)}
      </td>
      <td className="px-3 py-3 text-right tabular-nums text-ink">
        {formatPlanningPercent(choice.confidence, 1)}
      </td>
      <td className="min-w-44 px-3 py-3 align-top">
        {constraints.length === 0 ? (
          <span className="text-ink-3">No binding constraints</span>
        ) : (
          <ul className="space-y-1">
            {constraints.map((constraint) => (
              <li key={constraint}>
                <Badge variant="warn">{constraint}</Badge>
              </li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  );
}

function ChoiceComparison({
  row,
  currency,
  comparisonId,
}: {
  row: PlanningRunSelectionRecord;
  currency: string;
  comparisonId: string;
}) {
  const detail = row.detail;
  return (
    <div
      id={comparisonId}
      role="region"
      aria-labelledby={`${comparisonId}-title`}
      className="border-t border-line bg-panel-2/40 px-3 py-4"
    >
      <div className="mb-3 flex flex-col gap-1">
        <p
          id={`${comparisonId}-title`}
          className="text-sm font-semibold text-ink"
        >
          Choice comparison for {row.decision_key}
        </p>
        <p className="max-w-4xl text-xs leading-relaxed text-ink-2">
          {detail.selected_reason}
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[64rem] border-collapse text-left text-xs">
          <caption className="sr-only">
            Current policy, selected plan, and rejected alternatives with
            reconciled numeric evidence
          </caption>
          <thead>
            <tr className="border-y border-line text-ink-3">
              <th scope="col" className="px-3 py-2 font-medium">Plan state and choice</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Spend</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Service</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Shortage</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">AOG risk</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Objective</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Confidence</th>
              <th scope="col" className="px-3 py-2 font-medium">Constraints</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            <ChoiceComparisonRow
              state="Current policy"
              choice={detail.current}
              currency={currency}
            />
            <ChoiceComparisonRow
              state="Selected plan"
              choice={detail.selected}
              currency={currency}
              selected
            />
            {detail.rejected_alternatives.map(
              (alternative: PlanningRejectedAlternative) => (
                <ChoiceComparisonRow
                  key={alternative.candidate.candidate_id}
                  state="Rejected"
                  choice={alternative.candidate}
                  currency={currency}
                  reason={alternative.reason}
                />
              ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SelectionDetails({
  rows,
  currency,
}: {
  rows: PlanningRunSelectionRecord[];
  currency: string;
}) {
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  if (rows.length === 0) {
    return (
      <p className="text-sm text-ink-2">
        The completed run has no selection rows available.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[60rem] border-collapse text-left text-xs">
        <caption className="sr-only">
          Selected candidate and objective contribution for each planning key
        </caption>
        <thead>
          <tr className="border-b border-line text-ink-3">
            <th scope="col" className="px-2 py-2 font-medium">Decision key</th>
            <th scope="col" className="px-2 py-2 font-medium">Choice</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">Spend</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">Service</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">Shortage value</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">AOG value</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">Cost penalties</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">Objective</th>
            <th scope="col" className="px-2 py-2 font-medium">Floors</th>
          </tr>
        </thead>
        {rows.map((row) => {
          const selection = row.selection;
          const objective = selection.objective;
          const floors = selection.floor_states ?? [];
          const detail = row.detail;
          const expanded = expandedKey === row.decision_key;
          const comparisonId = `planning-comparison-${row.selected_candidate_id}`;
          return (
            <tbody key={row.decision_key}>
              <tr className="border-b border-line align-top">
                <th scope="row" className="px-2 py-3 font-medium text-ink">
                  <button
                    type="button"
                    aria-label={`Compare choices for ${row.decision_key}`}
                    aria-expanded={expanded}
                    aria-controls={comparisonId}
                    onClick={() =>
                      setExpandedKey((current) =>
                        current === row.decision_key
                          ? null
                          : row.decision_key,
                      )
                    }
                    className="rounded-sm text-left underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                  >
                    {row.decision_key}
                  </button>
                </th>
                <td className="px-2 py-3">
                  <div className="flex flex-col items-start gap-1">
                    <span className="text-ink">
                      {detail.selected?.label ?? row.selected_candidate_id}
                    </span>
                    {row.selected_is_no_change && (
                      <Badge variant="default">No change</Badge>
                    )}
                  </div>
                </td>
                <td className="px-2 py-3 text-right tabular-nums text-ink">
                  {formatPlanningMoney(row.acquisition_cash, currency)}
                </td>
                <td className="px-2 py-3 text-right tabular-nums text-ink">
                  {formatPlanningPercent(selection.expected_service_level)}
                </td>
                <td className="px-2 py-3 text-right tabular-nums text-ink">
                  {formatPlanningNumber(objective.shortage_value, 3)}
                </td>
                <td className="px-2 py-3 text-right tabular-nums text-ink">
                  {formatPlanningNumber(objective.aog_value, 3)}
                </td>
                <td className="px-2 py-3 text-right tabular-nums text-ink">
                  {formatPlanningNumber(
                    Number(objective.holding_penalty) +
                      Number(objective.ordering_penalty),
                    3,
                  )}
                </td>
                <td className="px-2 py-3 text-right font-medium tabular-nums text-ink">
                  {formatPlanningNumber(row.objective, 3)}
                </td>
                <td className="px-2 py-3">
                  {floors.length === 0 ? (
                    <span className="text-ink-3">None</span>
                  ) : (
                    <ul className="space-y-1">
                      {floors.map((floor) => (
                        <li key={floor.floor_id}>
                          <Badge variant={floor.binding ? "warn" : "good"}>
                            {floor.floor_id}
                            {floor.binding ? " · binding" : " · satisfied"}
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
              {expanded && (
                <tr>
                  <td colSpan={9} className="p-0">
                    <ChoiceComparison
                      row={row}
                      currency={currency}
                      comparisonId={comparisonId}
                    />
                  </td>
                </tr>
              )}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}

function ActiveRun({ run }: { run: PlanningRunView }) {
  const total = Math.max(run.progress_total, 1);
  const completed = Math.min(run.progress_completed, total);
  const percent = (completed / total) * 100;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {run.status === "queued" ? "Waiting for a worker" : "Optimization in progress"}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div
          role="progressbar"
          aria-label="Planning run progress"
          aria-valuemin={0}
          aria-valuemax={run.progress_total}
          aria-valuenow={run.progress_completed}
          aria-valuetext={`${run.progress_completed} of ${run.progress_total} keys`}
          className="h-2 overflow-hidden rounded-full bg-panel-2"
        >
          <div
            className="h-full rounded-full bg-brand transition-[width]"
            style={{ width: `${percent}%` }}
          />
        </div>
        <p role="status" aria-live="polite" className="text-sm text-ink-2">
          {run.progress_completed.toLocaleString("en-US")} of{" "}
          {run.progress_total.toLocaleString("en-US")} keys processed. This
          view refreshes while the run is active.
        </p>
        <p className="text-xs text-ink-3">
          Attempt {run.attempts.toLocaleString("en-US")} · started{" "}
          {formatPlanningDate(run.started_at)}
        </p>
      </CardContent>
    </Card>
  );
}

function InfeasibleRun({ run }: { run: PlanningRunView }) {
  const evidence = run.infeasibility;
  return (
    <Card>
      <CardHeader>
        <CardTitle>No feasible portfolio under these hard constraints</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <MetricTile
            label="Submitted budget"
            value={formatPlanningMoney(run.budget, run.currency)}
          />
          <MetricTile
            label="Minimum budget required"
            value={formatPlanningMoney(
              evidence?.minimum_budget_required,
              run.currency,
            )}
          />
          <MetricTile
            label="Budget shortfall"
            value={formatPlanningMoney(evidence?.budget_shortfall, run.currency)}
          />
        </div>
        {(evidence?.infeasible_key_count ?? 0) > 0 && (
          <div className="text-sm">
            <p className="font-medium text-ink">Keys without a feasible choice</p>
            <p className="mt-1 text-ink-2">
              {evidence?.infeasible_key_sample.join(", ") || "Sample unavailable"}
              {(evidence?.infeasible_key_count ?? 0) >
                (evidence?.infeasible_key_sample.length ?? 0)
                ? ` · showing ${evidence?.infeasible_key_sample.length ?? 0} of ${evidence?.infeasible_key_count.toLocaleString("en-US")}`
                : ""}
            </p>
          </div>
        )}
        {(evidence?.infeasible_floor_count ?? 0) > 0 && (
          <div className="text-sm">
            <p className="font-medium text-ink">Binding mandatory floors</p>
            <p className="mt-1 text-ink-2">
              {evidence?.infeasible_floor_sample.join(", ") || "Sample unavailable"}
              {(evidence?.infeasible_floor_count ?? 0) >
                (evidence?.infeasible_floor_sample.length ?? 0)
                ? ` · showing ${evidence?.infeasible_floor_sample.length ?? 0} of ${evidence?.infeasible_floor_count.toLocaleString("en-US")}`
                : ""}
            </p>
          </div>
        )}
        <p role="status" className="text-sm text-warn">
          Increase the acquisition budget, revise the listed floor only with
          policy-owner approval, or recompute missing candidate inputs. No
          actionable selections were produced.
        </p>
      </CardContent>
    </Card>
  );
}

export function PlanningRunDetail({
  run,
  tenant,
}: {
  run: PlanningRunView;
  tenant: string;
}) {
  const [selectionKey, setSelectionKey] = useState("");
  const [selectionKind, setSelectionKind] = useState<
    "all" | "changed" | "no_change"
  >("all");
  const [selectionOffset, setSelectionOffset] = useState(0);
  useEffect(() => {
    setSelectionKey("");
    setSelectionKind("all");
    setSelectionOffset(0);
  }, [run.run_id]);
  const selectionLimit = 25;
  const selectionsQuery = usePlanningRunSelections(run, tenant, {
    limit: selectionLimit,
    offset: selectionOffset,
    decisionKey: selectionKey.trim() || undefined,
    selectedIsNoChange:
      selectionKind === "all" ? undefined : selectionKind === "no_change",
  });
  const summary = run.summary;
  const solver = run.solver;
  const weights = run.model_profile.objective_weights;
  const confidenceSummary = summary?.confidence_summary;
  const summaryBudget = Number(summary?.budget ?? 0);
  const averageSelectedConfidence =
    confidenceSummary && summary && summary.selected_key_count > 0
      ? Number(confidenceSummary.selected_confidence_total) /
        summary.selected_key_count
      : null;

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Run {run.run_id.slice(0, 8)}</CardTitle>
              <p className="mt-1 text-xs text-ink-2">
                Submitted {formatPlanningDate(run.created_at)} by{" "}
                {run.submitted_by}
              </p>
            </div>
            <div className="flex flex-wrap gap-1">
              <Badge
                variant={
                  run.status === "completed"
                    ? "good"
                    : run.status === "failed"
                      ? "bad"
                      : run.status === "infeasible"
                        ? "warn"
                        : "brand"
                }
              >
                {planningStatusLabel(run.status)}
              </Badge>
              <Badge variant="warn">Advisory only</Badge>
              {run.stale === true && <Badge variant="warn">Stale inputs</Badge>}
              {run.stale === null && (
                <Badge variant="default">Staleness unavailable</Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {run.stale && (
            <p role="status" className="rounded-control border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
              {run.stale_reason}
            </p>
          )}
          <dl className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
            <div>
              <dt className="text-ink-3">Scope</dt>
              <dd className="font-medium text-ink">
                {run.key_count.toLocaleString("en-US")} keys ·{" "}
                {run.scope.kind === "all_eligible"
                  ? "all eligible"
                  : "explicit preview"}
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Budget</dt>
              <dd className="font-medium text-ink">
                {formatPlanningMoney(run.budget, run.currency)}
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Horizon</dt>
              <dd className="font-medium text-ink">{run.horizon_days} days</dd>
            </div>
            <div>
              <dt className="text-ink-3">Updated</dt>
              <dd className="font-medium text-ink">
                {formatPlanningDate(run.updated_at)}
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {(run.status === "queued" || run.status === "running") && (
        <ActiveRun run={run} />
      )}

      {run.status === "completed" && summary && (
        <Card>
          <CardHeader>
            <CardTitle>Portfolio result</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
              <MetricTile
                label="Selected spend"
                value={formatPlanningMoney(
                  summary.selected_acquisition_cash,
                  summary.currency,
                )}
                note={`${formatPlanningMoney(summary.budget_slack, summary.currency)} slack`}
              />
              <MetricTile
                label="Selected objective"
                value={formatPlanningNumber(summary.selected_objective, 3)}
              />
              <MetricTile
                label="Average service"
                value={formatPlanningPercent(summary.average_service_level)}
                note={`${formatPlanningNumber(summary.expected_shortage)} expected shortage`}
              />
              <MetricTile
                label="Keys selected"
                value={summary.selected_key_count.toLocaleString("en-US")}
                note={`${summary.no_change_key_count.toLocaleString("en-US")} no change`}
              />
              {confidenceSummary && (
                <MetricTile
                  label="Selected confidence"
                  value={formatPlanningPercent(averageSelectedConfidence, 1)}
                  note={`${formatPlanningPercent(
                    confidenceSummary.minimum_selected_confidence,
                    1,
                  )} minimum · ${confidenceSummary.low_confidence_key_count.toLocaleString(
                    "en-US",
                  )} below ${formatPlanningPercent(
                    confidenceSummary.low_confidence_threshold,
                    0,
                  )}`}
                />
              )}
              {summary.warning_count !== null &&
                summary.warning_count !== undefined && (
                  <MetricTile
                    label="Reconciled warnings"
                    value={summary.warning_count.toLocaleString("en-US")}
                    note="Matches persisted run warning evidence"
                  />
                )}
            </div>
            {summaryBudget > 0 ? (
              <div>
                <div
                  role="meter"
                  aria-label="Acquisition budget used"
                  aria-valuemin={0}
                  aria-valuemax={summaryBudget}
                  aria-valuenow={Number(summary.selected_acquisition_cash)}
                  className="h-2 overflow-hidden rounded-full bg-panel-2"
                >
                  <div
                    className="h-full rounded-full bg-good"
                    style={{
                      width: `${Math.min(
                        100,
                        (Number(summary.selected_acquisition_cash) /
                          summaryBudget) *
                          100,
                      )}%`,
                    }}
                  />
                </div>
                <p className="mt-1 text-xs text-ink-3">
                  Hard budget reconciles: selected spend plus slack equals{" "}
                  {formatPlanningMoney(summary.budget, summary.currency)}.
                </p>
              </div>
            ) : (
              <p role="status" className="text-xs text-ink-3">
                Zero acquisition budget; selected spend and slack both
                reconcile to{" "}
                {formatPlanningMoney(summary.budget, summary.currency)}.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {run.status === "infeasible" && <InfeasibleRun run={run} />}

      {run.status === "failed" && (
        <Card>
          <CardHeader>
            <CardTitle>Run failed safely</CardTitle>
          </CardHeader>
          <CardContent>
            <p role="alert" className="text-sm text-bad">
              {run.detail.error_code ?? "planning_run_failed"}
            </p>
            {run.detail.guidance && (
              <p className="mt-2 text-sm text-ink-2">{run.detail.guidance}</p>
            )}
            <p className="mt-2 text-xs text-ink-3">
              No selections or writeback authority are exposed from this state.
            </p>
          </CardContent>
        </Card>
      )}

      {solver && <SolverEvidence solver={solver} />}
      <CoverageDisclosure run={run} />
      <ObjectiveWeights weights={weights} />

      {run.status === "completed" && (
        <Card>
          <CardHeader>
            <CardTitle>Key selections and objective ledger</CardTitle>
            <p className="text-xs text-ink-2">
              Expand a decision key to compare the current policy, selected
              plan, and nearest rejected alternatives with their binding
              evidence.
            </p>
          </CardHeader>
          <CardContent>
            <div
              role="group"
              aria-label="Selection filters"
              className="mb-4 flex flex-wrap items-end gap-3"
            >
              <label className="flex min-w-[15rem] flex-1 flex-col gap-1 text-xs font-medium text-ink-2">
                Exact decision key
                <input
                  value={selectionKey}
                  onChange={(event) => {
                    setSelectionKey(event.target.value);
                    setSelectionOffset(0);
                  }}
                  placeholder="PN@LOCATION"
                  className="h-9 rounded-control border border-line bg-panel px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                />
              </label>
              <label className="flex flex-col gap-1 text-xs font-medium text-ink-2">
                Choice type
                <select
                  value={selectionKind}
                  onChange={(event) => {
                    setSelectionKind(
                      event.target.value as "all" | "changed" | "no_change",
                    );
                    setSelectionOffset(0);
                  }}
                  className="h-9 rounded-control border border-line bg-panel px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                >
                  <option value="all">All choices</option>
                  <option value="changed">Changed only</option>
                  <option value="no_change">No change only</option>
                </select>
              </label>
            </div>
            {selectionsQuery.isPending ? (
              <QueryLoading
                label="Loading selection details…"
                className="text-sm text-ink-2"
              />
            ) : selectionsQuery.isError ? (
              <QueryError
                label="Failed to load selection details"
                error={selectionsQuery.error}
                onRetry={() => selectionsQuery.refetch()}
                className="flex flex-col items-start gap-3 text-sm text-bad"
              />
            ) : (
              <>
                <SelectionDetails
                  rows={selectionsQuery.data?.items ?? []}
                  currency={run.currency}
                />
                <div className="mt-3 flex items-center justify-between gap-3 text-xs text-ink-2">
                  <span role="status" aria-live="polite">
                    {selectionsQuery.data?.total.toLocaleString("en-US") ?? 0}{" "}
                    matching selections
                  </span>
                  <span className="flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={selectionOffset === 0}
                      onClick={() =>
                        setSelectionOffset((value) =>
                          Math.max(0, value - selectionLimit),
                        )
                      }
                    >
                      Previous
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={
                        selectionOffset + selectionLimit >=
                        (selectionsQuery.data?.total ?? 0)
                      }
                      onClick={() =>
                        setSelectionOffset((value) => value + selectionLimit)
                      }
                    >
                      Next
                    </Button>
                  </span>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}

      {run.assumption_diff.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Changes from parent run</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-2 text-xs">
              {run.assumption_diff.map((change, index) => (
                <div
                  key={`${String(change.field)}-${index}`}
                  className="grid grid-cols-[minmax(8rem,1fr)_1fr_1fr] gap-3 border-t border-line pt-2"
                >
                  <dt className="font-medium text-ink">
                    {String(change.field ?? "Assumption")}
                  </dt>
                  <dd className="text-ink-2">
                    Before: {String(change.before ?? "Not set")}
                  </dd>
                  <dd className="text-ink-2">
                    After: {String(change.after ?? "Not set")}
                  </dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      )}

      {(run.warnings.total > 0 || run.skipped_keys.total > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>Coverage warnings</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 text-xs md:grid-cols-2">
            <div>
              <p className="font-medium text-ink">Warnings</p>
              {run.warnings.total === 0 ? (
                <p className="mt-1 text-ink-3">None reported.</p>
              ) : (
                <>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-ink-2">
                  {run.warnings.by_code.map((warning) => (
                    <li key={warning.code}>
                      {warning.code} ({warning.count.toLocaleString("en-US")})
                    </li>
                  ))}
                  </ul>
                  {run.warnings.code_list_truncated && (
                    <p className="mt-2 text-ink-3">
                      Bounded code summary counted{" "}
                      {run.warnings.counted_items.toLocaleString("en-US")} of{" "}
                      {run.warnings.total.toLocaleString("en-US")} records.
                    </p>
                  )}
                </>
              )}
            </div>
            <div>
              <p className="font-medium text-ink">Skipped keys</p>
              {run.skipped_keys.total === 0 ? (
                <p className="mt-1 text-ink-3">None reported.</p>
              ) : (
                <>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-ink-2">
                  {run.skipped_keys.by_code.map((skipped) => (
                    <li key={skipped.code}>
                      {skipped.code} ({skipped.count.toLocaleString("en-US")})
                    </li>
                  ))}
                  </ul>
                  {run.skipped_keys.code_list_truncated && (
                    <p className="mt-2 text-ink-3">
                      Bounded code summary counted{" "}
                      {run.skipped_keys.counted_items.toLocaleString("en-US")} of{" "}
                      {run.skipped_keys.total.toLocaleString("en-US")} records.
                    </p>
                  )}
                </>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Immutable lineage</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-3 text-xs md:grid-cols-2">
            <div>
              <dt className="text-ink-3">Planning fingerprint</dt>
              <dd className="break-all font-mono text-ink">
                {run.planning_fingerprint}
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Submitted source snapshot</dt>
              <dd className="break-all font-mono text-ink">
                {run.source_snapshot_hash}
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Submitted source generation</dt>
              <dd className="break-all font-mono text-ink">
                {run.source_generation_hash}
              </dd>
            </div>
            {run.current_source_snapshot_hash && (
              <div>
                <dt className="text-ink-3">Current source snapshot</dt>
                <dd className="break-all font-mono text-ink">
                  {run.current_source_snapshot_hash}
                </dd>
              </div>
            )}
            {run.current_source_generation_hash && (
              <div>
                <dt className="text-ink-3">Current source generation</dt>
                <dd className="break-all font-mono text-ink">
                  {run.current_source_generation_hash}
                </dd>
              </div>
            )}
            <div>
              <dt className="text-ink-3">Model profile</dt>
              <dd className="text-ink-2">
                Policy {String(run.model_profile.tenant_policy_version ?? "unavailable")} ·
                forecast {String(run.model_profile.forecast_version ?? "unavailable")} ·
                repair {String(run.model_profile.repair_model_version ?? "unavailable")} ·
                optimizer {String(run.model_profile.optimizer_version ?? "unavailable")}
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
