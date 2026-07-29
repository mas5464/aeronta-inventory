import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { activeTenant, request } from "@/lib/api/client";

export type ReplayRunStatus = "queued" | "running" | "completed" | "failed";
export type ReplayComparisonRule = "matched_budget" | "matched_service";

export interface ReplayMetrics {
  currency: string;
  outcome_manifest_sha256: string;
  demanded_units: string;
  filled_units: string;
  backordered_units: string;
  shortage_unit_days: string;
  ending_inventory_units: string;
  inventory_investment: string;
  holding_cost: string;
  ordering_cost: string;
  acquisition_cash: string;
  aog_risk_proxy_events: string;
  decision_count: number;
  fill_rate: string;
}

export interface ReplayMetricDelta {
  fill_rate: string;
  backordered_units: string;
  shortage_unit_days: string;
  inventory_investment: string;
  holding_cost: string;
  ordering_cost: string;
  acquisition_cash: string;
  aog_risk_proxy_events: string;
}

export interface ReplayCohort {
  criticality_tier: number;
  demand_regime: string;
  repairability: string;
  location_code: string;
  repair_data_confidence: string;
  evidence_artifact_id: string;
}

export interface ReplayCohortResult {
  cohort_id: string;
  cohort: ReplayCohort;
  observation_count: number;
  current: ReplayMetrics;
  challenger: ReplayMetrics;
  delta: ReplayMetricDelta;
}

export interface ReplayMetricDefinition {
  metric: string;
  unit: string;
  denominator: string;
  exclusions: string;
}

export interface ReplayExclusionCount {
  reason_code: string;
  count: number;
}

export interface ReplayObservationLineage {
  observation_id: string;
  decision_key: string;
  as_of: string;
  horizon_end: string;
  cohort_id: string;
  source_snapshot_hash: string;
  outcome_manifest_sha256: string;
  current_planning_fingerprint: string;
  challenger_planning_fingerprint: string;
  current_request_sha256: string;
  challenger_request_sha256: string;
}

export interface ReplayUniverseDecision {
  observation_id: string;
  tenant_id: string;
  decision_key: string;
  as_of: string;
  horizon_end: string;
}

export interface ReplayExclusion extends ReplayUniverseDecision {
  reason_code: string;
  detail: string;
}

export interface ReplayScorecardHeader {
  contract_version: "replay.v1";
  tenant_id: string;
  currency: string;
  universe_id: string;
  universe_sha256: string;
  current_policy_label: string;
  challenger_policy_label: string;
  comparison_rule: ReplayComparisonRule;
  comparison_rule_definition: string;
  match_tolerance: string;
  advisory_only: true;
  observation_count: number;
  total_observation_count: number;
  excluded_observation_count: number;
  coverage_rate: string;
  exclusions_by_reason: ReplayExclusionCount[];
  current: ReplayMetrics;
  challenger: ReplayMetrics;
  delta: ReplayMetricDelta;
  metric_definitions: ReplayMetricDefinition[];
  universe_decision_count: number;
  cohort_count: number;
  lineage_count: number;
  source_snapshot_hash_count: number;
  planning_fingerprint_count: number;
  universe_decisions_sha256: string;
  exclusions_sha256: string;
  observation_lineage_sha256: string;
  cohorts_sha256: string;
  source_snapshot_hashes_sha256: string;
  planning_fingerprints_sha256: string;
}

export interface ReplayPlanLineage {
  observation_id: string;
  policy: "current" | "challenger";
  as_of: string;
  source_snapshot_hash: string;
  planning_fingerprint: string;
  planning_request_sha256: string;
  forecast_version: string;
  repair_model_version: string;
  tenant_policy_version: string;
  candidate_planner_version: string;
  objective_version: string;
  solver: Record<string, unknown>;
}

export interface ReplayDecisionLineageEvidence {
  tenant_id: string;
  as_of: string;
  source_snapshot_hash: string;
  planning_fingerprint: string;
  planning_request_sha256: string;
  forecast_version: string;
  repair_model_version: string;
  tenant_policy_version: string;
  candidate_planner_version: string;
  objective_version: string;
  solver: Record<string, unknown>;
  artifacts?: unknown[];
}

export interface ReplayRun {
  replay_id: string;
  replay_fingerprint: string;
  input_sha256: string;
  contract_version: "replay.v1";
  status: ReplayRunStatus;
  universe_ref: string;
  universe_id: string;
  universe_sha256: string;
  comparison_rule: ReplayComparisonRule;
  expected_decision_count: number;
  advisory_only: true;
  scorecard: ReplayScorecardHeader | null;
  coverage_rate: string | null;
  detail: {
    error_code?: string;
    guidance?: string;
    writeback_capability?: "none";
    comparison_rule_definition?: string;
    review_package?: {
      input_sha256: string;
      universe_sha256: string;
      trusted_input_sha256: string;
      lineage_count: number;
      exclusion_count: number;
      cohort_count: number;
    };
  };
  submitted_by: string;
  attempts: number;
  claimed_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReplayLineageBundle {
  reference: ReplayObservationLineage;
  current: ReplayDecisionLineageEvidence;
  challenger: ReplayDecisionLineageEvidence;
  outcome: Record<string, unknown>;
}

export interface ReplayLineageRecord {
  observation_id: string;
  decision_key: string;
  as_of: string;
  horizon_end: string;
  cohort_id: string;
  lineage: ReplayLineageBundle;
}

export interface ReplayExclusionRecord {
  observation_id: string;
  decision_key: string;
  as_of: string;
  horizon_end: string;
  reason_code: string;
  exclusion: ReplayExclusion;
}

export interface ReplayCohortRecord {
  cohort_id: string;
  observation_count: number;
  cohort: ReplayCohortResult;
}

export interface ReplayEvidencePage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReplayPageParams {
  limit?: number;
  offset?: number;
  observationId?: string;
}

export interface ReplayExclusionPageParams extends ReplayPageParams {
  reasonCode?: string;
}

export interface ReplayRunSubmission {
  run: ReplayRun;
  created: boolean;
}

export interface CreateReplayRunBody {
  universe_ref: string;
  currency: string;
  current_policy_label: string;
  challenger_policy_label: string;
  comparison_rule: ReplayComparisonRule;
  match_tolerance: string;
}

export interface ReplayUniverseMetadata {
  universe_ref: string;
  universe_id: string;
  universe_sha256: string;
  contract_version: string;
  currency: string;
  expected_decision_count: number;
  observation_count: number;
  exclusion_count: number;
  created_at: string;
}

export interface ReplayUniversePage {
  items: ReplayUniverseMetadata[];
  total: number;
  limit: number;
  offset: number;
}

export function replayRunsQueryKey(tenant: string) {
  return ["replay-runs", tenant] as const;
}

export function replayUniversesQueryKey(tenant: string) {
  return ["replay-runs", tenant, "universes"] as const;
}

export function replayRunsPollInterval(
  runs: ReplayRun[] | undefined,
): number | false {
  return runs?.some(
    (run) => run.status === "queued" || run.status === "running",
  )
    ? 2_000
    : false;
}

export function replayEvidenceQueryKey(
  tenant: string,
  replayId: string,
  resource: "lineage" | "exclusions" | "cohorts",
  params: ReplayExclusionPageParams = {},
) {
  return [
    "replay-runs",
    tenant,
    replayId,
    resource,
    {
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
      observationId: params.observationId ?? null,
      reasonCode: params.reasonCode ?? null,
    },
  ] as const;
}

function replayPageQuery(
  params: ReplayExclusionPageParams,
): URLSearchParams {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  if (params.observationId) {
    query.set("observation_id", params.observationId);
  }
  if (params.reasonCode) {
    query.set("reason_code", params.reasonCode);
  }
  return query;
}

export function getReplayRuns(
  tenant: string = activeTenant(),
  limit = 20,
): Promise<ReplayRun[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  return request<ReplayRun[]>(
    `/v1/tenants/${encodeURIComponent(tenant)}/replay-runs?${query.toString()}`,
  );
}

export function getReplayRun(
  replayId: string,
  tenant: string = activeTenant(),
): Promise<ReplayRun> {
  return request<ReplayRun>(
    `/v1/tenants/${encodeURIComponent(tenant)}/replay-runs/${encodeURIComponent(replayId)}`,
  );
}

export function getReplayUniverses(
  tenant: string = activeTenant(),
  limit = 50,
  offset = 0,
): Promise<ReplayUniversePage> {
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return request<ReplayUniversePage>(
    `/v1/tenants/${encodeURIComponent(tenant)}/replay-runs/universes?${query.toString()}`,
  );
}

export function submitReplayRun(
  body: CreateReplayRunBody,
  tenant: string = activeTenant(),
): Promise<ReplayRunSubmission> {
  return request<ReplayRunSubmission>(
    `/v1/tenants/${encodeURIComponent(tenant)}/replay-runs`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

export function getReplayLineagePage(
  replayId: string,
  tenant: string = activeTenant(),
  params: ReplayPageParams = {},
): Promise<ReplayEvidencePage<ReplayLineageRecord>> {
  const query = replayPageQuery(params);
  return request<ReplayEvidencePage<ReplayLineageRecord>>(
    `/v1/tenants/${encodeURIComponent(tenant)}/replay-runs/${encodeURIComponent(replayId)}/lineage?${query.toString()}`,
  );
}

export function getReplayExclusionPage(
  replayId: string,
  tenant: string = activeTenant(),
  params: ReplayExclusionPageParams = {},
): Promise<ReplayEvidencePage<ReplayExclusionRecord>> {
  const query = replayPageQuery(params);
  return request<ReplayEvidencePage<ReplayExclusionRecord>>(
    `/v1/tenants/${encodeURIComponent(tenant)}/replay-runs/${encodeURIComponent(replayId)}/exclusions?${query.toString()}`,
  );
}

export function getReplayCohortPage(
  replayId: string,
  tenant: string = activeTenant(),
  params: Pick<ReplayPageParams, "limit" | "offset"> = {},
): Promise<ReplayEvidencePage<ReplayCohortRecord>> {
  const query = replayPageQuery(params);
  return request<ReplayEvidencePage<ReplayCohortRecord>>(
    `/v1/tenants/${encodeURIComponent(tenant)}/replay-runs/${encodeURIComponent(replayId)}/cohorts?${query.toString()}`,
  );
}

export function useReplayRuns(tenant: string = activeTenant()) {
  return useQuery<ReplayRun[]>({
    queryKey: replayRunsQueryKey(tenant),
    queryFn: () => getReplayRuns(tenant),
    enabled: Boolean(tenant),
    staleTime: 15_000,
    refetchInterval: (query) =>
      replayRunsPollInterval(query.state.data),
  });
}

export function useReplayUniverses(
  tenant: string = activeTenant(),
  enabled = true,
) {
  return useQuery<ReplayUniversePage>({
    queryKey: replayUniversesQueryKey(tenant),
    queryFn: () => getReplayUniverses(tenant),
    enabled: enabled && Boolean(tenant),
    staleTime: 30_000,
  });
}

export function useSubmitReplayRun(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<
    ReplayRunSubmission,
    Error,
    CreateReplayRunBody,
    { submittedTenant: string }
  >({
    mutationFn: (body) => submitReplayRun(body, tenant),
    onMutate: () => ({ submittedTenant: tenant }),
    onSuccess: (submission, _body, context) => {
      if (!context) return;
      const key = replayRunsQueryKey(context.submittedTenant);
      queryClient.setQueryData<ReplayRun[]>(key, (current = []) => [
        submission.run,
        ...current.filter(
          (run) => run.replay_id !== submission.run.replay_id,
        ),
      ]);
      void queryClient.invalidateQueries({ queryKey: key, exact: true });
    },
  });
}

export function useReplayLineagePage(
  replayId: string,
  tenant: string = activeTenant(),
  params: ReplayPageParams = {},
  enabled = true,
) {
  return useQuery<ReplayEvidencePage<ReplayLineageRecord>>({
    queryKey: replayEvidenceQueryKey(tenant, replayId, "lineage", params),
    queryFn: () => getReplayLineagePage(replayId, tenant, params),
    enabled: enabled && Boolean(tenant) && Boolean(replayId),
    staleTime: Infinity,
  });
}

export function useReplayExclusionPage(
  replayId: string,
  tenant: string = activeTenant(),
  params: ReplayExclusionPageParams = {},
  enabled = true,
) {
  return useQuery<ReplayEvidencePage<ReplayExclusionRecord>>({
    queryKey: replayEvidenceQueryKey(tenant, replayId, "exclusions", params),
    queryFn: () => getReplayExclusionPage(replayId, tenant, params),
    enabled: enabled && Boolean(tenant) && Boolean(replayId),
    staleTime: Infinity,
  });
}

export function useReplayCohortPage(
  replayId: string,
  tenant: string = activeTenant(),
  params: Pick<ReplayPageParams, "limit" | "offset"> = {},
  enabled = true,
) {
  return useQuery<ReplayEvidencePage<ReplayCohortRecord>>({
    queryKey: replayEvidenceQueryKey(tenant, replayId, "cohorts", params),
    queryFn: () => getReplayCohortPage(replayId, tenant, params),
    enabled: enabled && Boolean(tenant) && Boolean(replayId),
    staleTime: Infinity,
  });
}
