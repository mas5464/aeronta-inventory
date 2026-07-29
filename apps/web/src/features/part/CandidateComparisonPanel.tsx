import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  CandidateActionLine,
  CandidateFrontier,
  CandidateModelIdentity,
  CandidateTargetLevels,
  PolicyCandidate,
} from "@/lib/api/types";
import {
  formatCandidateDecimal,
  formatCandidateLabel,
  formatCandidateMoney,
  formatCandidatePercent,
} from "@/features/part/candidateView";

function PolicyLevels({
  current,
  target,
}: {
  current: CandidateTargetLevels;
  target: CandidateTargetLevels;
}) {
  const rows = [
    ["Reorder point", current.rop, target.rop],
    ["EOQ", current.eoq, target.eoq],
    ["Safety stock", current.safety_stock, target.safety_stock],
    ["Maximum stock", current.max_stock, target.max_stock],
  ] as const;

  return (
    <table className="w-full text-left text-xs">
      <caption className="sr-only">Current and target policy levels</caption>
      <thead>
        <tr className="text-ink-2">
          <th scope="col" className="pb-2 font-medium">Policy level</th>
          <th scope="col" className="pb-2 text-right font-medium">Current</th>
          <th scope="col" className="pb-2 text-right font-medium">Target</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, currentValue, targetValue]) => (
          <tr key={label} className="border-t border-line">
            <th scope="row" className="py-1.5 font-normal text-ink-2">{label}</th>
            <td className="py-1.5 text-right tabular-nums">{currentValue}</td>
            <td className="py-1.5 text-right tabular-nums">{targetValue}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ModelIdentity({ identity }: { identity: CandidateModelIdentity }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
      <dt className="text-ink-2">Forecast</dt>
      <dd>
        <span className="font-medium">{identity.forecast_model}</span>
        <span className="text-ink-2"> · {identity.forecast_version}</span>
      </dd>
      <dt className="text-ink-2">Policy</dt>
      <dd>
        <span className="font-medium">{identity.policy_model}</span>
        <span className="text-ink-2"> · {identity.policy_version}</span>
      </dd>
      {identity.repair_model && identity.repair_version && (
        <>
          <dt className="text-ink-2">Repair</dt>
          <dd>
            <span className="font-medium">{identity.repair_model}</span>
            <span className="text-ink-2"> · {identity.repair_version}</span>
          </dd>
        </>
      )}
      {identity.member_forecasts.length > 0 && (
        <>
          <dt className="text-ink-2">Members</dt>
          <dd>
            <ul className="space-y-1">
              {identity.member_forecasts.map((member) => (
                <li key={member.decision_key}>
                  {member.decision_key}: {member.forecast_model} ·{" "}
                  {member.forecast_version}
                </li>
              ))}
            </ul>
          </dd>
        </>
      )}
    </dl>
  );
}

function actionRoute(action: CandidateActionLine): string | null {
  if (action.source_location && action.destination_location) {
    return `${action.source_location} → ${action.destination_location}`;
  }
  if (action.source_location) return `From ${action.source_location}`;
  if (action.destination_location) return `To ${action.destination_location}`;
  return null;
}

function CandidateCard({ candidate }: { candidate: PolicyCandidate }) {
  const { lifecycle_costs: costs, outcome, reconciliation } = candidate;
  const bindingConstraints = candidate.constraints.filter(
    (constraint) => constraint.binding,
  );
  const titleId = `candidate-${candidate.candidate_id}`;

  return (
    <article
      aria-labelledby={titleId}
      className="rounded-card border border-line bg-panel-2 p-4"
      data-testid={`candidate-${candidate.is_no_change ? "no-change" : "alternative"}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line pb-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h4 id={titleId} className="font-semibold text-ink">
              {candidate.is_no_change ? "Current / no change" : candidate.label}
            </h4>
            {candidate.is_no_change && <Badge>Baseline</Badge>}
            {candidate.feasible ? (
              <Badge variant="good">Feasible</Badge>
            ) : (
              <Badge variant="bad">Infeasible</Badge>
            )}
            <Badge variant="brand">
              {formatCandidateLabel(candidate.candidate_kind)}
            </Badge>
          </div>
          {candidate.is_no_change && (
            <p className="mt-1 text-xs text-ink-2">{candidate.label}</p>
          )}
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-right text-xs">
          <dt className="text-ink-2">Action quantity</dt>
          <dd className="font-medium tabular-nums">
            {formatCandidateDecimal(candidate.action_quantity)}
          </dd>
          <dt className="text-ink-2">Acquisition cash</dt>
          <dd className="font-medium tabular-nums">
            {formatCandidateMoney(costs.currency, costs.acquisition_cash)}
          </dd>
        </dl>
      </div>

      <div className="mt-4 grid gap-5 xl:grid-cols-2">
        <section aria-label="Target policy levels">
          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">
            Target levels
          </h5>
          <PolicyLevels
            current={candidate.current_levels}
            target={candidate.target_levels}
          />
        </section>

        <section aria-label="Served model identity">
          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">
            Models actually served
          </h5>
          <ModelIdentity identity={candidate.model_identity} />
        </section>

        <section aria-label="Actions">
          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">
            Finalized actions
          </h5>
          <ul className="space-y-2 text-xs">
            {candidate.actions.map((action) => {
              const route = actionRoute(action);
              return (
                <li key={action.line_id} className="rounded border border-line p-2">
                  <div className="flex flex-wrap justify-between gap-2">
                    <span className="font-medium">
                      {formatCandidateLabel(action.kind)}
                    </span>
                    <span className="tabular-nums">
                      {formatCandidateDecimal(action.quantity)} units
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-ink-2">
                    {route && <span>{route}</span>}
                    <span>
                      Unit acquisition:{" "}
                      {formatCandidateMoney(
                        action.currency,
                        action.unit_acquisition_cash,
                      )}
                    </span>
                    {action.source_reference && (
                      <span>Reference: {action.source_reference}</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>

        <section aria-label="Expected outcome">
          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">
            Expected outcome
          </h5>
          <dl className="grid grid-cols-[1fr_max-content] gap-x-3 gap-y-1 text-xs">
            <dt className="text-ink-2">Service level</dt>
            <dd className="text-right tabular-nums">
              {formatCandidatePercent(outcome.expected_service_level)}
            </dd>
            <dt className="text-ink-2">Expected shortage</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateDecimal(outcome.expected_shortage)} units
            </dd>
            <dt className="text-ink-2">Expected excess</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateDecimal(outcome.expected_excess)} units
            </dd>
            <dt className="text-ink-2">Expected AOG risk</dt>
            <dd className="text-right tabular-nums">
              {formatCandidatePercent(outcome.expected_aog_risk)}
            </dd>
            <dt className="text-ink-2">Confidence</dt>
            <dd className="text-right tabular-nums">
              {formatCandidatePercent(candidate.confidence)}
            </dd>
            <dt className="text-ink-2">Ending net position</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateDecimal(outcome.ending_net_position)} units
            </dd>
          </dl>
        </section>

        <section aria-label="Lifecycle cost components">
          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">
            Lifecycle cost
          </h5>
          <dl className="grid grid-cols-[1fr_max-content] gap-x-3 gap-y-1 text-xs">
            <dt className="text-ink-2">Acquisition cash</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateMoney(costs.currency, costs.acquisition_cash)}
            </dd>
            <dt className="text-ink-2">Holding</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateMoney(costs.currency, costs.holding_cost)}
            </dd>
            <dt className="text-ink-2">Ordering</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateMoney(costs.currency, costs.ordering_cost)}
            </dd>
            <dt className="text-ink-2">Shortage</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateMoney(costs.currency, costs.shortage_cost)}
            </dd>
            <dt className="text-ink-2">Other</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateMoney(costs.currency, costs.other_cost)}
            </dd>
            <dt className="border-t border-line pt-1 font-medium">Total lifecycle</dt>
            <dd className="border-t border-line pt-1 text-right font-semibold tabular-nums">
              {formatCandidateMoney(costs.currency, costs.total_lifecycle_cost)}
            </dd>
          </dl>
          <p className="mt-2 text-xs text-ink-2" data-testid="lifecycle-reconciliation">
            {formatCandidateMoney(costs.currency, costs.total_lifecycle_cost)} ={" "}
            {formatCandidateDecimal(costs.acquisition_cash)} +{" "}
            {formatCandidateDecimal(costs.holding_cost)} +{" "}
            {formatCandidateDecimal(costs.ordering_cost)} +{" "}
            {formatCandidateDecimal(costs.shortage_cost)} +{" "}
            {formatCandidateDecimal(costs.other_cost)}
          </p>
        </section>

        <section aria-label="Quantity reconciliation">
          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">
            Quantity reconciliation
          </h5>
          <p
            className="rounded border border-line bg-panel p-2 font-mono text-xs tabular-nums"
            data-testid="quantity-reconciliation"
          >
            {formatCandidateDecimal(reconciliation.available_before)} +{" "}
            {formatCandidateDecimal(reconciliation.expected_receipts_before)} +{" "}
            {formatCandidateDecimal(reconciliation.transfer_in_quantity)} +{" "}
            {formatCandidateDecimal(reconciliation.purchase_quantity)} −{" "}
            {formatCandidateDecimal(reconciliation.outbound_quantity)} −{" "}
            {formatCandidateDecimal(reconciliation.projected_demand)} ={" "}
            {formatCandidateDecimal(reconciliation.ending_net_position)}
          </p>
          <p className="mt-2 text-xs text-ink-2">
            Available + expected receipts + transfer in + purchase − outbound −
            projected demand = ending net position
          </p>
          <dl className="mt-2 grid grid-cols-[1fr_max-content] gap-x-3 gap-y-1 text-xs">
            <dt className="text-ink-2">Total inbound</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateDecimal(reconciliation.total_inbound_quantity)}
            </dd>
            <dt className="text-ink-2">Reconciled action quantity</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateDecimal(reconciliation.action_quantity)}
            </dd>
            <dt className="text-ink-2">Reconciled expected shortage</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateDecimal(reconciliation.expected_shortage)}
            </dd>
            <dt className="text-ink-2">Reconciled acquisition cash</dt>
            <dd className="text-right tabular-nums">
              {formatCandidateMoney(
                reconciliation.currency,
                reconciliation.acquisition_cash,
              )}
            </dd>
          </dl>
        </section>
      </div>

      <div className="mt-5 grid gap-5 border-t border-line pt-4 lg:grid-cols-2">
        <section aria-label="Binding constraints">
          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">
            Binding constraints
          </h5>
          {bindingConstraints.length === 0 ? (
            <p className="text-xs text-ink-2">No binding constraints reported.</p>
          ) : (
            <ul className="space-y-2 text-xs">
              {bindingConstraints.map((constraint) => (
                <li key={constraint.constraint_id}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{constraint.constraint_id}</span>
                    <Badge>{constraint.scope}</Badge>
                    <Badge variant={constraint.satisfied ? "good" : "bad"}>
                      {constraint.satisfied ? "Satisfied" : "Not satisfied"}
                    </Badge>
                    {constraint.hard && <Badge variant="warn">Hard</Badge>}
                  </div>
                  <p className="mt-1 text-ink-2">
                    Source: {constraint.source}
                    {constraint.value !== null && ` · Value: ${constraint.value}`}
                  </p>
                  {constraint.detail && (
                    <p className="mt-1 text-ink-2">{constraint.detail}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label="Candidate evidence">
          <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">
            Evidence
          </h5>
          <ul className="space-y-2 text-xs">
            {candidate.evidence.map((evidence, index) => (
              <li key={`${evidence.kind}-${evidence.reference_id ?? index}`}>
                <p className="font-medium">
                  {formatCandidateLabel(evidence.kind)}
                </p>
                <p className="text-ink-2">
                  {evidence.detail} · Source: {evidence.source}
                  {evidence.reference_id && ` · Reference: ${evidence.reference_id}`}
                </p>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </article>
  );
}

export function CandidateComparisonPanel({
  frontier,
}: {
  frontier: CandidateFrontier;
}) {
  const visibleCandidates = frontier.candidates.filter(
    (candidate) => candidate.is_no_change || candidate.feasible,
  );
  const feasibleAlternatives = visibleCandidates.filter(
    (candidate) => !candidate.is_no_change,
  );
  const omittedInfeasible = frontier.candidates.length - visibleCandidates.length;

  return (
    <section aria-label="Candidate comparison">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Candidate comparison</CardTitle>
              <p className="mt-1 text-sm text-ink-2">
                Current/no-change baseline and feasible alternatives for{" "}
                {frontier.decision_key}.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge variant="brand">
                {frontier.total_options_considered} options considered
              </Badge>
              <Badge data-testid="dominated-options-count">
                {frontier.dominated_options_removed} dominated removed
              </Badge>
            </div>
          </div>
          <dl className="mt-3 grid gap-1 text-xs text-ink-2">
            <div className="grid grid-cols-[max-content_1fr] gap-2">
              <dt>Frontier fingerprint</dt>
              <dd
                className="break-all font-mono text-ink"
                data-testid="frontier-fingerprint"
              >
                {frontier.frontier_fingerprint}
              </dd>
            </div>
            <div className="grid grid-cols-[max-content_1fr] gap-2">
              <dt>Output digest</dt>
              <dd className="break-all font-mono text-ink">
                {frontier.output_digest}
              </dd>
            </div>
            <div className="grid grid-cols-[max-content_1fr] gap-2">
              <dt>Planner</dt>
              <dd className="text-ink">{frontier.planner_version}</dd>
            </div>
          </dl>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {visibleCandidates.map((candidate) => (
            <CandidateCard key={candidate.candidate_id} candidate={candidate} />
          ))}
          {feasibleAlternatives.length === 0 && (
            <p className="text-sm text-ink-2">
              No feasible alternative candidates were retained for this frontier.
            </p>
          )}
          {omittedInfeasible > 0 && (
            <p className="text-xs text-ink-2">
              {omittedInfeasible} infeasible{" "}
              {omittedInfeasible === 1 ? "option was" : "options were"} retained
              for audit but not presented as feasible alternatives.
            </p>
          )}
        </CardContent>
      </Card>
    </section>
  );
}
