import { useEffect, useRef, useState, type FormEvent } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import {
  type ReplayComparisonRule,
  type ReplayMetrics,
  type ReplayRun,
  useReplayCohortPage,
  useReplayExclusionPage,
  useReplayLineagePage,
  useReplayRuns,
  useReplayUniverses,
  useSubmitReplayRun,
} from "@/lib/api/replay";
import { activeTenant } from "@/lib/api/client";

const EVIDENCE_PAGE_SIZE = 25;
const replayInputClass =
  "h-9 w-full rounded-control border border-line bg-panel px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const replayLabelClass =
  "flex flex-col gap-1 text-xs font-medium text-ink-2";

function asNumber(value: string | number): number {
  return Number(value);
}

function formatNumber(value: string | number, digits = 1): string {
  return asNumber(value).toLocaleString("en-US", {
    maximumFractionDigits: digits,
  });
}

function formatCurrency(value: string | number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(asNumber(value));
}

function formatPercent(value: string | number): string {
  return `${(asNumber(value) * 100).toFixed(1)}%`;
}

function isBoundedReplayDecimal(value: string): boolean {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return false;
  const wholeDigits = match[1].replace(/^0+(?=\d)/, "").length;
  const fractionDigits = match[2]?.length ?? 0;
  return fractionDigits <= 12 && wholeDigits + fractionDigits <= 18;
}

function runStatusBadge(run: ReplayRun) {
  if (run.status === "completed") return <Badge variant="good">Completed</Badge>;
  if (run.status === "failed") return <Badge variant="bad">Failed safely</Badge>;
  return <Badge variant="warn">{run.status === "queued" ? "Queued" : "Running"}</Badge>;
}

function MetricRow({
  label,
  field,
  current,
  challenger,
  kind = "number",
}: {
  label: string;
  field: keyof ReplayMetrics;
  current: ReplayMetrics;
  challenger: ReplayMetrics;
  kind?: "number" | "currency" | "percent";
}) {
  const render = (metrics: ReplayMetrics) => {
    const value = metrics[field];
    if (kind === "currency") return formatCurrency(value, metrics.currency);
    if (kind === "percent") return formatPercent(value);
    return formatNumber(value);
  };
  return (
    <tr className="border-t border-line">
      <th scope="row" className="px-3 py-2 text-left font-medium text-ink">
        {label}
      </th>
      <td className="px-3 py-2 text-right tabular-nums">{render(current)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{render(challenger)}</td>
    </tr>
  );
}

function EvidencePager({
  label,
  offset,
  count,
  total,
  onOffsetChange,
}: {
  label: string;
  offset: number;
  count: number;
  total: number;
  onOffsetChange: (offset: number) => void;
}) {
  if (total <= EVIDENCE_PAGE_SIZE) return null;
  const first = count === 0 ? 0 : offset + 1;
  const last = offset + count;
  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-ink-2">
      <span>
        {label} {first}–{last} of {total}
      </span>
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={offset === 0}
          onClick={() =>
            onOffsetChange(Math.max(0, offset - EVIDENCE_PAGE_SIZE))
          }
        >
          Previous
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={last >= total}
          onClick={() => onOffsetChange(offset + EVIDENCE_PAGE_SIZE)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

function ReplayLaunchPanel({
  tenant,
  canSubmit,
}: {
  tenant: string;
  canSubmit: boolean;
}) {
  const universesQuery = useReplayUniverses(tenant, canSubmit);
  const submitMutation = useSubmitReplayRun(tenant);
  const currentTenant = useRef(tenant);
  currentTenant.current = tenant;
  const [universeRef, setUniverseRef] = useState("");
  const [currentLabel, setCurrentLabel] = useState("Current policy");
  const [challengerLabel, setChallengerLabel] = useState(
    "Repair-aware policy",
  );
  const [comparisonRule, setComparisonRule] =
    useState<ReplayComparisonRule>("matched_budget");
  const [matchTolerance, setMatchTolerance] = useState("0");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submissionNotice, setSubmissionNotice] = useState<{
    replayId: string;
    status: ReplayRun["status"];
    created: boolean;
  } | null>(null);

  useEffect(() => {
    setUniverseRef("");
    setValidationError(null);
    setSubmissionNotice(null);
  }, [tenant]);

  useEffect(() => {
    const items = universesQuery.data?.items ?? [];
    if (
      items.length > 0 &&
      !items.some((universe) => universe.universe_ref === universeRef)
    ) {
      setUniverseRef(items[0].universe_ref);
    }
  }, [universeRef, universesQuery.data?.items]);

  if (!canSubmit) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Read-only replay evidence</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-ink-2">
            Viewers may inspect bounded replay results and evidence. A planner,
            admin, or owner role is required to select a trusted universe or
            submit a new replay.
          </p>
        </CardContent>
      </Card>
    );
  }

  if (universesQuery.isPending) {
    return <QueryLoading label="Loading trusted replay universes…" />;
  }
  if (universesQuery.isError) {
    return (
      <QueryError
        label="Failed to load trusted replay universes"
        error={universesQuery.error}
        onRetry={() => void universesQuery.refetch()}
      />
    );
  }

  const universes = universesQuery.data.items;
  if (universes.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No trusted replay universes available</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-ink-2">
          <p>
            An external data pipeline must generate a validated package and a
            service-role importer must register its tenant-scoped universe
            before a replay can be submitted.
          </p>
          <p>
            Historical facts cannot be entered or uploaded from this browser.
          </p>
        </CardContent>
      </Card>
    );
  }

  const selected = universes.find(
    (universe) => universe.universe_ref === universeRef,
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    const toleranceText = matchTolerance.trim();
    if (!selected) {
      setValidationError("Select a trusted replay universe.");
      return;
    }
    if (!currentLabel.trim() || !challengerLabel.trim()) {
      setValidationError("Both policy labels are required.");
      return;
    }
    if (!isBoundedReplayDecimal(toleranceText)) {
      setValidationError(
        "Match tolerance must be a non-negative decimal with at most 18 digits and 12 decimal places.",
      );
      return;
    }
    setValidationError(null);
    setSubmissionNotice(null);
    submitMutation.mutate(
      {
        universe_ref: selected.universe_ref,
        currency: selected.currency,
        current_policy_label: currentLabel.trim(),
        challenger_policy_label: challengerLabel.trim(),
        comparison_rule: comparisonRule,
        match_tolerance: toleranceText,
      },
      {
        onSuccess: (submission) => {
          if (currentTenant.current === tenant) {
            setSubmissionNotice({
              replayId: submission.run.replay_id,
              status: submission.run.status,
              created: submission.created,
            });
          }
        },
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Submit historical shadow replay</CardTitle>
        <p className="text-xs text-ink-2">
          Select opaque trusted metadata. Historical observations, outcomes,
          and lineage remain server-side.
        </p>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
          <label className={replayLabelClass}>
            Trusted replay universe
            <select
              aria-label="Trusted replay universe"
              value={universeRef}
              onChange={(event) => setUniverseRef(event.target.value)}
              className={replayInputClass}
            >
              {universes.map((universe) => (
                <option
                  key={universe.universe_ref}
                  value={universe.universe_ref}
                >
                  {universe.universe_id} · {universe.observation_count} observed
                  {" · "}
                  {universe.exclusion_count} excluded
                </option>
              ))}
            </select>
          </label>

          {selected && (
            <p className="text-xs text-ink-3">
              {selected.expected_decision_count} declared decisions ·{" "}
              {selected.currency} · published{" "}
              {new Date(selected.created_at).toLocaleDateString()}
            </p>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <label className={replayLabelClass}>
              Current policy label
              <input
                aria-label="Current policy label"
                maxLength={120}
                value={currentLabel}
                onChange={(event) => setCurrentLabel(event.target.value)}
                className={replayInputClass}
              />
            </label>
            <label className={replayLabelClass}>
              Challenger policy label
              <input
                aria-label="Challenger policy label"
                maxLength={120}
                value={challengerLabel}
                onChange={(event) => setChallengerLabel(event.target.value)}
                className={replayInputClass}
              />
            </label>
          </div>

          <fieldset className="flex flex-col gap-2 rounded-control border border-line p-3">
            <legend className="px-1 text-xs font-semibold text-ink-2">
              Comparison rule
            </legend>
            <label className="flex items-start gap-2 text-sm text-ink">
              <input
                type="radio"
                name="replay-comparison-rule"
                value="matched_budget"
                checked={comparisonRule === "matched_budget"}
                onChange={() => setComparisonRule("matched_budget")}
              />
              Matched acquisition budget
            </label>
            <label className="flex items-start gap-2 text-sm text-ink">
              <input
                type="radio"
                name="replay-comparison-rule"
                value="matched_service"
                checked={comparisonRule === "matched_service"}
                onChange={() => setComparisonRule("matched_service")}
              />
              Matched service level
            </label>
          </fieldset>

          <label className={replayLabelClass}>
            Match tolerance
            <input
              aria-label="Match tolerance"
              inputMode="decimal"
              value={matchTolerance}
              onChange={(event) => setMatchTolerance(event.target.value)}
              className={replayInputClass}
            />
          </label>

          {(validationError || submitMutation.error) && (
            <p role="alert" className="text-xs text-bad">
              {validationError ?? submitMutation.error?.message}
            </p>
          )}
          {submissionNotice && (
            <p role="status" className="text-xs text-good">
              {submissionNotice.status === "queued" ||
              submissionNotice.status === "running"
                ? `${
                    submissionNotice.created ? "Replay" : "Existing replay"
                  } ${submissionNotice.replayId.slice(0, 8)} ${
                    submissionNotice.status
                  }. Status polling is active.`
                : submissionNotice.created
                  ? `Replay ${submissionNotice.replayId.slice(0, 8)} ${submissionNotice.status}.`
                  : `Existing replay ${submissionNotice.replayId.slice(0, 8)} is already ${submissionNotice.status}. No duplicate run was created.`}
            </p>
          )}
          <Button
            type="submit"
            disabled={submitMutation.isPending || !selected}
          >
            {submitMutation.isPending
              ? "Submitting replay…"
              : "Submit historical replay"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

export interface ShadowValidationPanelProps {
  tenant?: string;
  canSubmit?: boolean;
}

export function ShadowValidationPanel({
  tenant = activeTenant(),
  canSubmit = false,
}: ShadowValidationPanelProps) {
  const { data, isPending, isError, error, refetch } = useReplayRuns(tenant);
  const run = data?.[0];
  const evidenceEnabled = run?.status === "completed" && run.scorecard !== null;
  const [lineageOffset, setLineageOffset] = useState(0);
  const [exclusionOffset, setExclusionOffset] = useState(0);
  const [cohortOffset, setCohortOffset] = useState(0);
  const lineageQuery = useReplayLineagePage(
    run?.replay_id ?? "",
    tenant,
    { limit: EVIDENCE_PAGE_SIZE, offset: lineageOffset },
    evidenceEnabled,
  );
  const exclusionQuery = useReplayExclusionPage(
    run?.replay_id ?? "",
    tenant,
    { limit: EVIDENCE_PAGE_SIZE, offset: exclusionOffset },
    evidenceEnabled,
  );
  const cohortQuery = useReplayCohortPage(
    run?.replay_id ?? "",
    tenant,
    { limit: EVIDENCE_PAGE_SIZE, offset: cohortOffset },
    evidenceEnabled,
  );

  useEffect(() => {
    setLineageOffset(0);
    setExclusionOffset(0);
    setCohortOffset(0);
  }, [run?.replay_id, tenant]);

  const launchPanel = (
    <ReplayLaunchPanel
      key={tenant}
      tenant={tenant}
      canSubmit={canSubmit}
    />
  );

  if (isPending) {
    return (
      <div className="space-y-4">
        {launchPanel}
        <QueryLoading label="Loading historical shadow validation…" />
      </div>
    );
  }
  if (isError) {
    return (
      <div className="space-y-4">
        {launchPanel}
        <QueryError
          label="Failed to load historical shadow validation"
          error={error}
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  if (!run) {
    return (
      <div className="space-y-4">
        {launchPanel}
        <Card aria-label="Historical shadow validation">
          <CardHeader>
            <CardTitle>Historical shadow validation</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-ink-2">
            <Badge variant="warn">Advisory only</Badge>
            <p>No historical replay has been submitted for this tenant.</p>
            <p>
              Replay evaluates matched historical decisions only. It cannot
              create purchases, transfers, repair routes, or policy writebacks.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const scorecard = run.scorecard;
  return (
    <div className="space-y-4">
      {launchPanel}
      <section aria-labelledby="shadow-validation-title" className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="shadow-validation-title" className="text-lg font-semibold text-ink">
            Historical shadow validation
          </h2>
          <p className="mt-1 text-sm text-ink-2">
            Matched historical comparison from immutable as-of evidence. This is
            validation evidence, not a causal guarantee of future performance.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2" aria-live="polite">
          <Badge variant="warn">Advisory only · no writeback</Badge>
          {runStatusBadge(run)}
        </div>
      </header>

      {run.status === "failed" && (
        <div role="alert" className="rounded-control border border-bad/40 bg-bad/10 p-3 text-sm">
          <p className="font-medium text-bad">Replay stopped without a scorecard.</p>
          <p className="mt-1 text-ink-2">
            {run.detail.guidance ??
              "Review the replay evidence and submit a new immutable run."}
          </p>
          {run.detail.error_code && (
            <p className="mt-1 font-mono text-xs text-ink-3">
              Error code: {run.detail.error_code}
            </p>
          )}
        </div>
      )}

      {(run.status === "queued" || run.status === "running") && (
        <div role="status" aria-live="polite" className="rounded-control border border-line p-3 text-sm text-ink-2">
          The worker is validating the complete historical universe. No partial
          or actionable-looking result is displayed while it runs.
        </div>
      )}

      {scorecard && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Portfolio comparison</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <div>
                  <p className="text-xs text-ink-3">Coverage</p>
                  <p className="text-lg font-semibold tabular-nums text-ink">
                    {formatPercent(scorecard.coverage_rate)}
                  </p>
                  <p className="text-xs text-ink-2">
                    {scorecard.observation_count} evaluated ·{" "}
                    {scorecard.excluded_observation_count} excluded ·{" "}
                    {scorecard.total_observation_count} declared
                  </p>
                </div>
                <div>
                  <p className="text-xs text-ink-3">Comparison rule</p>
                  <p className="font-medium text-ink">
                    {scorecard.comparison_rule === "matched_budget"
                      ? "Matched budget"
                      : "Matched service"}
                  </p>
                  <p className="text-xs text-ink-2">
                    {scorecard.comparison_rule_definition}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-ink-3">Universe</p>
                  <p className="font-medium text-ink">{scorecard.universe_id}</p>
                  <p className="font-mono text-xs text-ink-3" title={scorecard.universe_sha256}>
                    {scorecard.universe_sha256.slice(0, 16)}…
                  </p>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <caption className="sr-only">
                    Current and challenger historical outcomes
                  </caption>
                  <thead>
                    <tr className="border-b border-line text-ink-2">
                      <th scope="col" className="px-3 py-2 text-left">Outcome</th>
                      <th scope="col" className="px-3 py-2 text-right">
                        {scorecard.current_policy_label}
                      </th>
                      <th scope="col" className="px-3 py-2 text-right">
                        {scorecard.challenger_policy_label}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <MetricRow label="Fill rate" field="fill_rate" kind="percent" current={scorecard.current} challenger={scorecard.challenger} />
                    <MetricRow label="Backordered units" field="backordered_units" current={scorecard.current} challenger={scorecard.challenger} />
                    <MetricRow label="Shortage unit-days" field="shortage_unit_days" current={scorecard.current} challenger={scorecard.challenger} />
                    <MetricRow label="Inventory investment" field="inventory_investment" kind="currency" current={scorecard.current} challenger={scorecard.challenger} />
                    <MetricRow label="Holding cost" field="holding_cost" kind="currency" current={scorecard.current} challenger={scorecard.challenger} />
                    <MetricRow label="Ordering cost" field="ordering_cost" kind="currency" current={scorecard.current} challenger={scorecard.challenger} />
                    <MetricRow label="AOG-risk proxy events" field="aog_risk_proxy_events" current={scorecard.current} challenger={scorecard.challenger} />
                  </tbody>
                </table>
              </div>

              {scorecard.exclusions_by_reason.length > 0 && (
                <div className="text-xs text-ink-2">
                  <p className="font-medium text-ink">Coverage exclusions</p>
                  <ul className="mt-1 list-disc pl-5">
                    {scorecard.exclusions_by_reason.map((item) => (
                      <li key={item.reason_code}>
                        {item.reason_code.replace(/_/g, " ")}: {item.count}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Exclusion evidence</CardTitle>
            </CardHeader>
            <CardContent>
              {exclusionQuery.isPending ? (
                <QueryLoading label="Loading replay exclusions…" />
              ) : exclusionQuery.isError ? (
                <QueryError
                  label="Failed to load replay exclusions"
                  error={exclusionQuery.error}
                  onRetry={() => void exclusionQuery.refetch()}
                />
              ) : exclusionQuery.data.items.length === 0 ? (
                <p className="text-sm text-ink-2">
                  No historical decisions were excluded from this replay.
                </p>
              ) : (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <caption className="sr-only">
                        Historical replay exclusion evidence
                      </caption>
                      <thead>
                        <tr className="border-b border-line text-ink-2">
                          <th scope="col" className="px-2 py-2 text-left">
                            Observation
                          </th>
                          <th scope="col" className="px-2 py-2 text-left">
                            Decision
                          </th>
                          <th scope="col" className="px-2 py-2 text-left">
                            Reason
                          </th>
                          <th scope="col" className="px-2 py-2 text-left">
                            Detail
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {exclusionQuery.data.items.map((item) => (
                          <tr
                            key={item.observation_id}
                            className="border-t border-line align-top"
                          >
                            <th
                              scope="row"
                              className="px-2 py-2 text-left font-medium text-ink"
                            >
                              {item.observation_id}
                            </th>
                            <td className="px-2 py-2">{item.decision_key}</td>
                            <td className="px-2 py-2">
                              {item.reason_code.replace(/_/g, " ")}
                            </td>
                            <td className="px-2 py-2">
                              {item.exclusion.detail}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <EvidencePager
                    label="Exclusions"
                    offset={exclusionOffset}
                    count={exclusionQuery.data.items.length}
                    total={exclusionQuery.data.total}
                    onOffsetChange={setExclusionOffset}
                  />
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Cohort reconciliation</CardTitle>
            </CardHeader>
            <CardContent>
              {cohortQuery.isPending ? (
                <QueryLoading label="Loading replay cohorts…" />
              ) : cohortQuery.isError ? (
                <QueryError
                  label="Failed to load replay cohorts"
                  error={cohortQuery.error}
                  onRetry={() => void cohortQuery.refetch()}
                />
              ) : cohortQuery.data.items.length === 0 ? (
                <p className="text-sm text-ink-2">
                  {scorecard.cohort_count === 0
                    ? "No cohorts were scored; every declared decision is represented in the exclusion ledger."
                    : "No cohorts are present on this evidence page."}
                </p>
              ) : (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <caption className="sr-only">
                        Historical outcomes segmented by approved replay cohorts
                      </caption>
                      <thead>
                        <tr className="border-b border-line text-ink-2">
                          <th scope="col" className="px-2 py-2 text-left">Criticality</th>
                          <th scope="col" className="px-2 py-2 text-left">Demand</th>
                          <th scope="col" className="px-2 py-2 text-left">Repairability</th>
                          <th scope="col" className="px-2 py-2 text-left">Location</th>
                          <th scope="col" className="px-2 py-2 text-left">Repair evidence</th>
                          <th scope="col" className="px-2 py-2 text-right">Decisions</th>
                          <th scope="col" className="px-2 py-2 text-right">Fill Δ</th>
                        </tr>
                      </thead>
                      <tbody>
                        {cohortQuery.data.items.map((item) => {
                          const result = item.cohort;
                          return (
                            <tr key={item.cohort_id} className="border-t border-line">
                              <td className="px-2 py-2">Tier {result.cohort.criticality_tier}</td>
                              <td className="px-2 py-2">{result.cohort.demand_regime}</td>
                              <td className="px-2 py-2">{result.cohort.repairability}</td>
                              <td className="px-2 py-2">{result.cohort.location_code}</td>
                              <td className="px-2 py-2">{result.cohort.repair_data_confidence}</td>
                              <td className="px-2 py-2 text-right tabular-nums">{item.observation_count}</td>
                              <td className="px-2 py-2 text-right tabular-nums">{formatPercent(result.delta.fill_rate)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <EvidencePager
                    label="Cohorts"
                    offset={cohortOffset}
                    count={cohortQuery.data.items.length}
                    total={cohortQuery.data.total}
                    onOffsetChange={setCohortOffset}
                  />
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Lineage and metric definitions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <div>
                <p className="font-medium text-ink">Immutable planning lineage</p>
                <p className="mt-1 text-xs text-ink-2">
                  {scorecard.source_snapshot_hash_count} source snapshot
                  {scorecard.source_snapshot_hash_count === 1 ? "" : "s"} ·{" "}
                  {scorecard.planning_fingerprint_count} planning fingerprint
                  {scorecard.planning_fingerprint_count === 1 ? "" : "s"} ·{" "}
                  {scorecard.lineage_count} scored decision
                  {scorecard.lineage_count === 1 ? "" : "s"}
                </p>
                <ul className="mt-2 space-y-1 font-mono text-xs text-ink-3">
                  <li title={scorecard.source_snapshot_hashes_sha256}>
                    Snapshot-set digest{" "}
                    {scorecard.source_snapshot_hashes_sha256.slice(0, 16)}…
                  </li>
                  <li title={scorecard.planning_fingerprints_sha256}>
                    Planning-set digest{" "}
                    {scorecard.planning_fingerprints_sha256.slice(0, 16)}…
                  </li>
                  <li title={scorecard.observation_lineage_sha256}>
                    Lineage digest{" "}
                    {scorecard.observation_lineage_sha256.slice(0, 16)}…
                  </li>
                </ul>
                {lineageQuery.isPending ? (
                  <QueryLoading label="Loading replay lineage…" />
                ) : lineageQuery.isError ? (
                  <QueryError
                    label="Failed to load replay lineage"
                    error={lineageQuery.error}
                    onRetry={() => void lineageQuery.refetch()}
                  />
                ) : lineageQuery.data.items.length > 0 ? (
                  <>
                    <div className="mt-3 overflow-x-auto">
                      <table className="w-full text-xs">
                        <caption className="sr-only">
                          Model, objective, candidate planner, and solver lineage
                        </caption>
                        <thead>
                          <tr className="border-b border-line text-ink-2">
                            <th scope="col" className="px-2 py-2 text-left">Decision</th>
                            <th scope="col" className="px-2 py-2 text-left">Policy</th>
                            <th scope="col" className="px-2 py-2 text-left">As of</th>
                            <th scope="col" className="px-2 py-2 text-left">Forecast</th>
                            <th scope="col" className="px-2 py-2 text-left">Repair model</th>
                            <th scope="col" className="px-2 py-2 text-left">Objective</th>
                            <th scope="col" className="px-2 py-2 text-left">Candidate planner</th>
                            <th scope="col" className="px-2 py-2 text-left">Solver</th>
                          </tr>
                        </thead>
                        <tbody>
                          {lineageQuery.data.items.flatMap((item) =>
                            (["current", "challenger"] as const).map((policy) => {
                              const lineage = item.lineage[policy];
                              return (
                                <tr
                                  key={`${item.observation_id}:${policy}`}
                                  className="border-t border-line align-top"
                                >
                                  <th scope="row" className="px-2 py-2 text-left font-medium text-ink">
                                    {item.observation_id}
                                  </th>
                                  <td className="px-2 py-2">{policy}</td>
                                  <td className="px-2 py-2">{lineage.as_of}</td>
                                  <td className="px-2 py-2">{lineage.forecast_version}</td>
                                  <td className="px-2 py-2">{lineage.repair_model_version}</td>
                                  <td className="px-2 py-2">{lineage.objective_version}</td>
                                  <td className="px-2 py-2">{lineage.candidate_planner_version}</td>
                                  <td className="px-2 py-2">
                                    {String(
                                      lineage.solver.termination ??
                                        lineage.solver.implementation ??
                                        "recorded",
                                    )}
                                  </td>
                                </tr>
                              );
                            }),
                          )}
                        </tbody>
                      </table>
                    </div>
                    <EvidencePager
                      label="Lineage records"
                      offset={lineageOffset}
                      count={lineageQuery.data.items.length}
                      total={lineageQuery.data.total}
                      onOffsetChange={setLineageOffset}
                    />
                  </>
                ) : (
                  <p className="mt-2 text-xs text-ink-2">
                    No evaluated plan lineage is present; every declared decision
                    is represented by the exclusion ledger.
                  </p>
                )}
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <caption className="sr-only">
                    Units, denominators, and exclusions for shadow metrics
                  </caption>
                  <thead>
                    <tr className="border-b border-line text-ink-2">
                      <th scope="col" className="px-2 py-2 text-left">Metric</th>
                      <th scope="col" className="px-2 py-2 text-left">Unit</th>
                      <th scope="col" className="px-2 py-2 text-left">Denominator</th>
                      <th scope="col" className="px-2 py-2 text-left">Exclusions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scorecard.metric_definitions.map((definition) => (
                      <tr key={definition.metric} className="border-t border-line align-top">
                        <th scope="row" className="px-2 py-2 text-left font-medium text-ink">
                          {definition.metric.replace(/_/g, " ")}
                        </th>
                        <td className="px-2 py-2">{definition.unit}</td>
                        <td className="px-2 py-2">{definition.denominator}</td>
                        <td className="px-2 py-2">{definition.exclusions}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
      </section>
    </div>
  );
}
