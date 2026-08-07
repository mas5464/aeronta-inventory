import { activeTenant, request } from "@/lib/api/client";

export type PlanningRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "infeasible"
  | "failed";

export interface PlanningCapability {
  contract_version: "planning-capability.v1";
  enabled: boolean;
  advisory_only: true;
  can_read: boolean;
  can_submit: boolean;
  reason_code:
    | "enabled"
    | "feature_disabled"
    | "insufficient_role";
}

export interface PlanningScopeKey {
  pn: string;
  location: string;
}

export interface PlanningObjectiveWeights {
  shortage_reduction_weight: string;
  aog_risk_reduction_weight: string;
  holding_cost_penalty_weight: string;
  ordering_cost_penalty_weight: string;
  criticality_weights: Record<string, string>;
}

export interface PlanningMandatoryFloor {
  floor_id: string;
  source: string;
  min_service_level?: string | null;
  max_expected_shortage?: string | null;
  max_aog_risk?: string | null;
  detail?: string | null;
}

export interface CreatePlanningRunBody {
  scope_kind: "explicit" | "all_eligible";
  keys: PlanningScopeKey[];
  budget: string;
  horizon_days: number;
  currency: string;
  objective_weights: PlanningObjectiveWeights;
  mandatory_floors: Record<string, PlanningMandatoryFloor[]>;
  time_limit_seconds: number;
  parent_run_id?: string | null;
}

export interface PlanningEvidenceCount {
  code: string;
  count: number;
}

export interface PlanningEvidenceSummary {
  total: number;
  counted_items: number;
  by_code: PlanningEvidenceCount[];
  code_list_truncated: boolean;
}

export interface PlanningObjectiveContribution {
  currency: string;
  criticality_weight: string;
  shortage_reduction: string;
  aog_risk_reduction: string;
  incremental_holding_cost: string;
  incremental_ordering_cost: string;
  shortage_value: string;
  aog_value: string;
  holding_penalty: string;
  ordering_penalty: string;
  total: string;
}

export interface PlanningFloorState {
  floor_id: string;
  source: string;
  satisfied: boolean;
  binding: boolean;
  detail?: string | null;
}

export interface PortfolioSelectionWire {
  decision_key: string;
  current_candidate_id: string;
  selected_candidate_id: string;
  selected_is_no_change: boolean;
  acquisition_cash: string;
  expected_shortage: string;
  expected_service_level: string;
  expected_aog_risk: string;
  objective: PlanningObjectiveContribution;
  floor_states: PlanningFloorState[];
}

export interface PortfolioSummaryWire {
  currency: string;
  budget: string;
  selected_acquisition_cash: string;
  budget_slack: string;
  selected_key_count: number;
  no_change_key_count: number;
  selected_objective: string;
  expected_shortage: string;
  average_service_level: string;
  maximum_aog_risk: string;
  warning_count?: number | null;
  confidence_summary?: PortfolioConfidenceSummaryWire | null;
}

export interface PortfolioConfidenceSummaryWire {
  selected_confidence_total: string;
  minimum_selected_confidence: string;
  low_confidence_threshold: string;
  low_confidence_key_count: number;
}

export interface SolverEvidenceWire {
  implementation: string;
  implementation_version: string;
  optimizer_version: string;
  termination: "optimal" | "not_proven" | "infeasible" | "failed";
  optimality_proven: boolean;
  objective: string | null;
  objective_bound: string | null;
  relative_gap: string | null;
  duration_ms: string;
  node_count: number | null;
  message: string;
}

export interface PortfolioResultWire {
  planning_fingerprint: string;
  tenant_id: string;
  status: "completed" | "infeasible" | "failed";
  selections: PortfolioSelectionWire[];
  summary: PortfolioSummaryWire | null;
  solver: SolverEvidenceWire;
  minimum_budget_required: string | null;
  budget_shortfall: string | null;
  infeasible_keys: string[];
  infeasible_floor_ids: string[];
}

export interface PlanningChoiceSnapshot {
  candidate_id: string;
  label: string;
  candidate_kind: string;
  acquisition_cash: string;
  expected_shortage: string;
  expected_service_level: string;
  expected_aog_risk: string;
  objective: PlanningObjectiveContribution;
  confidence: string;
  feasible: boolean;
  infeasibility_reasons: string[];
  hard_constraint_ids: string[];
  mandatory_floor_ids: string[];
}

export interface PlanningRejectedAlternative {
  candidate: PlanningChoiceSnapshot;
  reason_code: string;
  reason: string;
}

export interface PlanningSelectionDetail {
  decision_key: string;
  current: PlanningChoiceSnapshot;
  selected: PlanningChoiceSnapshot;
  selected_reason: string;
  rejected_alternatives: PlanningRejectedAlternative[];
}

export interface PlanningRunSafeDetail {
  error_code: string | null;
  guidance: string | null;
  retryable: boolean | null;
  failed_attempt: number | null;
  last_failed_attempt: number | null;
}

export interface PlanningAssumptionChange {
  field?: string;
  before?: string;
  after?: string;
  [key: string]: unknown;
}

export interface PlanningModelProfile {
  tenant_policy_version?: string;
  forecast_version?: string;
  repair_model_version?: string;
  candidate_planner_version?: string;
  optimizer_version?: string;
  objective_weights?: PlanningObjectiveWeights;
  time_limit_seconds?: number;
  [key: string]: unknown;
}

export interface PlanningTrustedModelProfile {
  tenant_policy_version: string;
  forecast_version: string;
  repair_model_version: string;
  candidate_planner_version: string;
}

export interface PlanningSavedModelProfile
  extends PlanningTrustedModelProfile {
  optimizer_version: string;
}

export interface PlanningRerunConfig {
  contract_version: "planning-rerun-config.v1";
  parent_run_id: string;
  scope_kind: "explicit" | "all_eligible";
  keys: PlanningScopeKey[];
  budget: string;
  horizon_days: number;
  currency: string;
  objective_weights: PlanningObjectiveWeights;
  mandatory_floors: Record<string, PlanningMandatoryFloor[]>;
  time_limit_seconds: number;
  source_generation_hash: string;
  parent_model_profile: PlanningSavedModelProfile;
  current_trusted_model_profile: PlanningTrustedModelProfile | null;
  repair_assumption_change_available: boolean;
  repair_assumption_mode: "current_trusted";
}

export interface PlanningCoverage {
  scope_key_count: number;
  authoritative_key_count: number;
  eligible_key_count: number;
  missing_candidate_frontier_key_count: number;
  criticality_unknown_key_count: number;
  candidate_menu_key_count: number;
  candidate_count: number;
  feasible_candidate_count: number;
  candidate_menu_coverage_rate: string;
  repair_model_key_count: number;
  repair_model_coverage_rate: string;
  repair_credit_key_count: number;
  repair_credit_coverage_rate: string;
  low_confidence_key_count: number;
  minimum_candidate_confidence: string | null;
  tat_confidence_status: "available" | "partial" | "unavailable";
  disclosure: string;
}

export interface PlanningScopeSummary {
  kind: "explicit" | "all_eligible";
  key_count: number;
  preview_keys: string[];
  preview_truncated: boolean;
}

export interface PlanningInfeasibilitySummary {
  minimum_budget_required: string | null;
  budget_shortfall: string | null;
  infeasible_key_count: number;
  infeasible_key_sample: string[];
  infeasible_floor_count: number;
  infeasible_floor_sample: string[];
}

export interface PlanningRunView {
  run_id: string;
  planning_fingerprint: string;
  contract_version: string;
  parent_run_id: string | null;
  parent_planning_fingerprint: string | null;
  parent_source_snapshot_hash: string | null;
  assumption_diff: PlanningAssumptionChange[];
  status: PlanningRunStatus;
  source_snapshot_hash: string;
  source_generation_hash: string;
  scope: PlanningScopeSummary;
  key_count: number;
  budget: string;
  horizon_days: number;
  currency: string;
  model_profile: PlanningModelProfile;
  advisory_only: boolean;
  progress_completed: number;
  progress_total: number;
  summary: PortfolioSummaryWire | null;
  infeasibility: PlanningInfeasibilitySummary | null;
  detail: PlanningRunSafeDetail;
  solver: SolverEvidenceWire | null;
  warnings: PlanningEvidenceSummary;
  skipped_keys: PlanningEvidenceSummary;
  submitted_by: string;
  attempts: number;
  claimed_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  coverage: PlanningCoverage | null;
  stale: boolean | null;
  current_source_snapshot_hash: string | null;
  current_source_generation_hash: string | null;
  stale_reason: string | null;
}

export interface PlanningRunSubmission {
  run: PlanningRunView;
  created: boolean;
}

export interface PlanningRunSelectionRecord {
  decision_key: string;
  current_candidate_id: string;
  selected_candidate_id: string;
  selected_is_no_change: boolean;
  acquisition_cash: string;
  objective: string;
  selection: PortfolioSelectionWire;
  detail: PlanningSelectionDetail;
}

export interface PlanningRunSelectionsPage {
  items: PlanningRunSelectionRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface PlanningRunSelectionParams {
  limit?: number;
  offset?: number;
  decisionKey?: string;
  selectedIsNoChange?: boolean;
}

const planningBase = (tenant: string) =>
  `/v1/tenants/${encodeURIComponent(tenant)}/planning-runs`;

export function createPlanningRun(
  body: CreatePlanningRunBody,
  tenant: string = activeTenant(),
): Promise<PlanningRunSubmission> {
  return request<PlanningRunSubmission>(planningBase(tenant), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getPlanningCapability(
  tenant: string = activeTenant(),
): Promise<PlanningCapability> {
  return request<PlanningCapability>(`${planningBase(tenant)}/capabilities`);
}

export function getPlanningRuns(
  tenant: string = activeTenant(),
  limit = 30,
): Promise<PlanningRunView[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  return request<PlanningRunView[]>(`${planningBase(tenant)}?${query.toString()}`);
}

export function getPlanningRun(
  runId: string,
  tenant: string = activeTenant(),
): Promise<PlanningRunView> {
  return request<PlanningRunView>(
    `${planningBase(tenant)}/${encodeURIComponent(runId)}`,
  );
}

export function getPlanningRunRerunConfig(
  runId: string,
  tenant: string = activeTenant(),
): Promise<PlanningRerunConfig> {
  return request<PlanningRerunConfig>(
    `${planningBase(tenant)}/${encodeURIComponent(runId)}/rerun-config`,
  );
}

export function getPlanningRunSelections(
  runId: string,
  tenant: string = activeTenant(),
  params: PlanningRunSelectionParams = {},
): Promise<PlanningRunSelectionsPage> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  if (params.decisionKey) query.set("decision_key", params.decisionKey);
  if (params.selectedIsNoChange !== undefined) {
    query.set("selected_is_no_change", String(params.selectedIsNoChange));
  }
  return request<PlanningRunSelectionsPage>(
    `${planningBase(tenant)}/${encodeURIComponent(runId)}/selections?${query.toString()}`,
  );
}
