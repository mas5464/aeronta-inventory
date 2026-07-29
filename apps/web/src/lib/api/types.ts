/**
 * TypeScript mirror of the BFF's wire models
 * (services/agent-spine/src/trax_io_spine/bff/models.py).
 *
 * Only the dashboard-related shapes are needed for Slice S1; extend this
 * file as later slices consume more of the BFF surface.
 */

export interface Breakdown {
  key: string;
  count: number;
  on_hand: number;
  shortage: number;
}

export interface PartShortfall {
  pn: string;
  location: string;
  shortage: number;
  on_hand: number;
  projected_demand: number;
}

export interface DashboardSummary {
  parts: number;
  total_on_hand: number;
  total_on_hand_value: number;
  total_shortage: number;
  total_projected_demand: number;
  aog_exposure: number;
  open_recommendations: number;
  net_cost_impact: number;
  by_criticality: Breakdown[];
  by_ata: Breakdown[];
  by_part_class: Breakdown[];
  by_tier: Breakdown[];
  top_shortages: PartShortfall[];
}

/**
 * Slice S2 — Part Drill-Down shapes, mirroring
 * services/agent-spine/src/trax_io_spine/bff/models.py `PartContext` et al.
 */

export interface PolicyView {
  rop: number;
  eoq: number;
  safety_stock: number;
  max_stock: number;
}

export interface StockBreakdown {
  on_hand: number;
  serviceable: number;
  in_repair: number;
  allocated: number;
  rental: number;
  loan: number;
}

export interface LeadTimeView {
  /** Legacy NEW-only compatibility projection. */
  promised_days: number | null;
  realized_mean_days: number | null;
  n_observations: number;
}

export type SupplyCycleCondition = "NEW" | "REP";
export type SupplyCycleStatus =
  | "observed"
  | "configured_fallback"
  | "unavailable";
export type SupplyCycleSource =
  | "order_plan_closed_orders"
  | "pn_vendor_price";
export type SupplyCycleGroupingLevel =
  | "part_vendor_condition"
  | "part_condition";
export type SupplyCycleConfidence = "high" | "medium" | "low" | "unknown";
export type SupplyCycleClassificationSource =
  | "explicit_order_type"
  | "legacy_order_id_prefix"
  | "configured_condition";
export type SupplyCycleProxyDefinition =
  | "order_creation_to_last_receipt"
  | "configured_repair_promise";
export type SupplyCycleProxyLabel =
  | "RO cycle-time proxy"
  | "Configured repair promise";

/**
 * Independent supply-cycle evidence lane. Metrics and provenance are null
 * when status is unavailable; the UI must not borrow values from another
 * condition to fill those gaps.
 */
export interface SupplyCycleLaneView {
  condition: SupplyCycleCondition;
  status: SupplyCycleStatus;
  mean_days: number | null;
  p50_days: number | null;
  p90_days: number | null;
  p99_days: number | null;
  n_observations: number;
  source: SupplyCycleSource | null;
  grouping_level: SupplyCycleGroupingLevel | null;
  confidence: SupplyCycleConfidence;
  data_cutoff: string | null;
  model_version: string | null;
  classification_source: SupplyCycleClassificationSource | null;
  proxy_definition: SupplyCycleProxyDefinition | null;
  proxy_label: SupplyCycleProxyLabel | null;
  unavailable_reason: string | null;
}

export interface OpenOrderView {
  order_id: string;
  order_type: string;
  vendor: string | null;
  qty_open: number;
  expected_rcv_date: string | null;
  /** Additive repair-pipeline lifecycle fields; absent on legacy snapshots. */
  order_line_id?: string | null;
  opened_at?: string | null;
  status?: string | null;
  serial_number?: string | null;
  location?: string | null;
  shop?: string | null;
}

export type RepairPipelineStatus = "available" | "partial" | "unavailable";

export type RepairWorkExclusionCode =
  | "missing_order_identity"
  | "missing_line_identity"
  | "missing_opened_at"
  | "future_opened_at"
  | "missing_location"
  | "location_mismatch"
  | "terminal_status"
  | "ineligible_status"
  | "duplicate_order_line"
  | "duplicate_serial"
  | "serial_quantity_mismatch"
  | "aggregate_wip_cap"
  | "unidentified_aggregate_residual";

export type RepairPipelineWarningCode =
  | "repair_pipeline_unavailable"
  | "repair_work_excluded"
  | "repair_identity_excluded"
  | "repair_age_missing"
  | "repair_source_duplicates"
  | "repair_wip_mismatch"
  | "repair_residual_unidentified";

export interface RepairWorkItem {
  contract_version: "repair-work-item.v1";
  tenant_id: string;
  repair_order_id: string;
  repair_line_id: string;
  part_number: string;
  quantity: number;
  location_code: string;
  opened_at: string;
  status: string;
  shop_code: string | null;
  vendor_code: string | null;
  serial_number: string | null;
}

export interface IncludedRepairPosition {
  work_item: RepairWorkItem;
  eligible_quantity: number;
  age_days: number;
}

export interface RepairWorkExclusion {
  repair_order_id: string | null;
  repair_line_id: string | null;
  serial_number: string | null;
  quantity: number;
  reason: RepairWorkExclusionCode;
  detail: string;
}

/**
 * Phase 5 reconciles identifiable repair orders to aggregate WIP without
 * forecasting a return date. `time_phased_credit_quantity` is deliberately
 * fixed at zero until the age-conditioned return model is introduced.
 */
export interface RepairPipeline {
  contract_version: "repair-pipeline.v1";
  tenant_id: string;
  part_number: string;
  location_code: string;
  as_of: string;
  status: RepairPipelineStatus;
  aggregate_wip_quantity: number;
  identified_open_quantity: number;
  unidentified_source_quantity: number;
  eligible_quantity: number;
  excluded_identifiable_quantity: number;
  aggregate_residual_quantity: number;
  source_overflow_quantity: number;
  time_phased_credit_quantity: 0;
  included: IncludedRepairPosition[];
  exclusions: RepairWorkExclusion[];
  warning_codes: RepairPipelineWarningCode[];
  evidence_source: "open_orders_snapshot+stock_position";
}

export interface RepairItemReturnProbability {
  repair_order_id: string;
  repair_line_id: string;
  serial_number: string | null;
  quantity: number;
  age_days: number;
  return_probability: number;
  serviceable_probability: number;
  expected_serviceable_units: number;
}

export interface RepairReturnHorizon {
  horizon_days: number;
  eligible_quantity: number;
  expected_units: number;
  variance_units: number;
  p10_units: number;
  p90_units: number;
  mean_serviceable_probability: number;
  item_probabilities: RepairItemReturnProbability[];
}

export interface RepairReturnEvidence {
  method:
    | "kaplan_meier"
    | "lognormal_quantile"
    | "deterministic_promise"
    | "unavailable";
  completed_observations: number;
  right_censored_observations: number;
  serviceable_yield: number;
  tat_multiplier: number;
  source: string;
  confidence: "high" | "medium" | "low" | "unavailable";
  data_cutoff: string | null;
  model_version: string;
  proxy_definition: string | null;
}

export interface RepairReturnProfile {
  contract_version: "repair-return-profile.v1";
  tenant_id: string;
  part_number: string;
  location_code: string;
  as_of: string;
  status: RepairPipelineStatus;
  eligible_quantity: number;
  excluded_quantity: number;
  aggregate_residual_quantity: number;
  horizons: RepairReturnHorizon[];
  exclusions: RepairWorkExclusion[];
  evidence: RepairReturnEvidence;
  warning_codes: string[];
}

export interface DemandPoint {
  period_start: string;
  removals: number;
  issues: number;
  total: number;
}

export interface DemandSummary {
  total_24mo: number;
  points: DemandPoint[];
}

export interface PartAttributesView {
  description: string;
  ata_chapter: string | null;
  part_class: string | null;
  shelf_life_days: number | null;
  hazardous_material: boolean;
  tool_control_item: boolean;
  criticality_tier: number | null;
}

export type PlanningEventCountSource =
  | "observed"
  | "bucket_fallback"
  | "unavailable";

export type PlanningDemandBucket = "day" | "week" | "month";
export type PlanningEvidenceAvailability =
  | "available"
  | "partial"
  | "unavailable";

export interface PlanningConstraintTrace {
  name: string;
  value: string | null;
  binding: boolean;
  source: string;
  /** Defaults to policy for constraints persisted before action scope existed. */
  scope?: "policy" | "action";
}

export type PlanningCalculationSource =
  | "served_calculation"
  | "legacy_reconstructed"
  | "unavailable";

export interface PlanningMemberTrace {
  pn: string;
  location: string;
  projection_kind: string;
  projected_historical_demand: number;
  scheduled_demand_status?: PlanningEvidenceAvailability;
  scheduled_demand_undated_lines?: number;
  scheduled_demand_undated_units?: number;
  scheduled_demand_due: number;
  projected_demand: number;
  dispatchable_available: number;
  open_receipts_status?: PlanningEvidenceAvailability;
  open_receipts_undated_lines?: number;
  open_receipts_undated_units?: number;
  open_receipts_due: number;
  overdue_open_receipts_due: number;
  repair_receipts_due: number;
  expected_receipts_due: number;
  net_position: number;
}

export interface PlanningTrace {
  /** Served evidence is exact; legacy values are reconstructed and explicitly qualified. */
  calculation_source?: PlanningCalculationSource;
  /** Optional during rollout; date-only values must be rendered without timezone shifting. */
  as_of?: string | null;
  /** Optional during rollout; inclusive upper boundary for due-date evidence. */
  horizon_end?: string | null;
  observation_start: string | null;
  observation_end: string | null;
  exposure_days: number;
  bucket: PlanningDemandBucket | null;
  observed_periods: number;
  zero_filled_periods: number;
  demand_event_count: number | null;
  event_count_source: PlanningEventCountSource;
  demanded_units: number;
  /** Raw observed units/exposure rate; it is not necessarily the served forecast rate. */
  historical_per_day: number;
  horizon_days: number;
  projection_kind?: string | null;
  /** Exact model rate used by the served recommendation; absent for legacy snapshots. */
  served_historical_per_day?: number | null;
  projected_historical_demand: number;
  scheduled_demand_status?: PlanningEvidenceAvailability;
  scheduled_demand_undated_lines?: number;
  scheduled_demand_undated_units?: number;
  scheduled_demand_due: number;
  projected_demand?: number | null;
  dispatchable_available?: number | null;
  open_receipts_status?: PlanningEvidenceAvailability;
  open_receipts_undated_lines?: number;
  open_receipts_undated_units?: number;
  open_receipts_due: number;
  /** Optional during rollout; subset of open_receipts_due already past as_of. */
  overdue_open_receipts_due?: number;
  repair_receipts_due?: number | null;
  expected_receipts_due?: number | null;
  net_position?: number | null;
  shortage_before_action?: number | null;
  pooled_group_id?: string | null;
  pooling_scope?: "single_key" | "complete_group" | "worklist_partial";
  excluded_member_keys?: string[];
  members?: PlanningMemberTrace[];
  constraints: PlanningConstraintTrace[];
  warnings: string[];
}

/**
 * Phase 2 — exact, versioned candidate-preview shapes.
 *
 * Pydantic serializes Decimal values as JSON strings. Keeping them as strings
 * here prevents the browser from silently rounding money or reconciliation
 * quantities before they reach the comparison UI.
 */
export type CandidateContractVersion = "candidate.v1";
export type CandidateDecimal = string;
export type CandidateActionKind =
  | "no_change"
  | "purchase"
  | "transfer_in"
  | "transfer_out"
  | "adjust_policy"
  | "reduce_stock"
  | "sell";
export type CandidateKind =
  | "no_change"
  | "purchase"
  | "transfer"
  | "transfer_purchase"
  | "adjust_policy"
  | "reduce_stock"
  | "sell";

export interface CandidateServedForecastIdentity {
  contract_version: CandidateContractVersion;
  decision_key: string;
  forecast_model: string;
  forecast_version: string;
}

export interface CandidateModelIdentity {
  contract_version: CandidateContractVersion;
  forecast_model: string;
  forecast_version: string;
  policy_model: string;
  policy_version: string;
  repair_model: string | null;
  repair_version: string | null;
  member_forecasts: CandidateServedForecastIdentity[];
}

export interface CandidateTargetLevels {
  contract_version: CandidateContractVersion;
  rop: number;
  eoq: number;
  safety_stock: number;
  max_stock: number;
}

export interface CandidateActionLine {
  contract_version: CandidateContractVersion;
  line_id: string;
  kind: CandidateActionKind;
  quantity: CandidateDecimal;
  currency: string;
  unit_acquisition_cash: CandidateDecimal;
  source_location: string | null;
  destination_location: string | null;
  source_reference: string | null;
}

export interface CandidateLifecycleCosts {
  contract_version: CandidateContractVersion;
  currency: string;
  acquisition_cash: CandidateDecimal;
  holding_cost: CandidateDecimal;
  ordering_cost: CandidateDecimal;
  shortage_cost: CandidateDecimal;
  other_cost: CandidateDecimal;
  total_lifecycle_cost: CandidateDecimal;
}

export interface CandidateOutcome {
  contract_version: CandidateContractVersion;
  projected_demand: CandidateDecimal;
  available_before: CandidateDecimal;
  expected_receipts_before: CandidateDecimal;
  inbound_quantity: CandidateDecimal;
  outbound_quantity: CandidateDecimal;
  ending_net_position: CandidateDecimal;
  expected_shortage: CandidateDecimal;
  expected_excess: CandidateDecimal;
  expected_service_level: CandidateDecimal;
  expected_aog_risk: CandidateDecimal;
}

export interface CandidateConstraintEvidence {
  contract_version: CandidateContractVersion;
  constraint_id: string;
  source: string;
  value: string | null;
  scope: "policy" | "action";
  hard: boolean;
  satisfied: boolean;
  binding: boolean;
  detail: string | null;
}

export interface CandidateEvidence {
  contract_version: CandidateContractVersion;
  kind: string;
  source: string;
  detail: string;
  reference_id: string | null;
}

export interface CandidateReconciliation {
  contract_version: CandidateContractVersion;
  currency: string;
  available_before: CandidateDecimal;
  expected_receipts_before: CandidateDecimal;
  projected_demand: CandidateDecimal;
  transfer_in_quantity: CandidateDecimal;
  purchase_quantity: CandidateDecimal;
  outbound_quantity: CandidateDecimal;
  total_inbound_quantity: CandidateDecimal;
  action_quantity: CandidateDecimal;
  ending_net_position: CandidateDecimal;
  expected_shortage: CandidateDecimal;
  acquisition_cash: CandidateDecimal;
}

export interface PolicyCandidate {
  contract_version: CandidateContractVersion;
  candidate_id: string;
  tenant_id: string;
  pn: string;
  location: string;
  decision_key: string;
  member_keys: string[];
  candidate_kind: CandidateKind;
  label: string;
  is_no_change: boolean;
  feasible: boolean;
  infeasibility_reasons: string[];
  model_identity: CandidateModelIdentity;
  current_levels: CandidateTargetLevels;
  target_levels: CandidateTargetLevels;
  actions: CandidateActionLine[];
  action_quantity: CandidateDecimal;
  lifecycle_costs: CandidateLifecycleCosts;
  outcome: CandidateOutcome;
  confidence: CandidateDecimal;
  constraints: CandidateConstraintEvidence[];
  evidence: CandidateEvidence[];
  reconciliation: CandidateReconciliation;
}

export interface CandidateFrontier {
  contract_version: CandidateContractVersion;
  frontier_fingerprint: string;
  output_digest: string;
  planner_version: "candidate-planner-v1";
  tenant_id: string;
  decision_key: string;
  member_keys: string[];
  currency: string;
  candidates: PolicyCandidate[];
  total_options_considered: number;
  dominated_options_removed: number;
}

export interface PartContext {
  pn: string;
  location: string;
  attributes: PartAttributesView;
  stock: StockBreakdown | null;
  current_policy: PolicyView | null;
  proposed_policy: PolicyView | null;
  lead_time: LeadTimeView | null;
  /**
   * Additive Phase 3 lanes. Current BFF responses always include both; the
   * optional boundary keeps pre-Phase-3 persisted/test payloads readable.
   */
  procurement_lead_time?: SupplyCycleLaneView;
  repair_cycle_time?: SupplyCycleLaneView;
  open_orders: OpenOrderView[];
  total_open_qty: number;
  /** Additive source coverage; absent legacy contexts are treated as unavailable. */
  open_orders_status?: PlanningEvidenceAvailability;
  demand: DemandSummary | null;
  unit_cost: number | null;
  /** Additive planning evidence; absent for legacy persisted part contexts. */
  planning_trace?: PlanningTrace | null;
  /** Additive Phase 2 preview; absent legacy contexts have no computed frontier. */
  candidate_frontier?: CandidateFrontier | null;
  /** Additive Phase 5 repair-WIP reconciliation; absent legacy contexts are unknown. */
  repair_pipeline?: RepairPipeline | null;
  /** Additive Phase 6 age-conditioned returns; absent is unknown/not applicable. */
  repair_return_profile?: RepairReturnProfile | null;
}

/**
 * Slice S3 — Workbench + AI Recommendations shapes, mirroring
 * services/agent-spine/src/trax_io_spine/bff/models.py (RecommendationType,
 * AutonomyTier, AogRiskLevel mirror trax_io_reco.contracts.enums).
 */

export type RecommendationType =
  | "purchase"
  | "transfer"
  | "reduce_stock"
  | "sell"
  | "adjust_min_max";

/** AutonomyTier — mirror only; 1=ADVISOR, 2=BOUNDED, 3=AUTONOMOUS. */
export type AutonomyTier = 1 | 2 | 3;

/** AogRiskLevel — 0=NONE .. 4=CRITICAL. */
export type AogRiskLevel = 0 | 1 | 2 | 3 | 4;

export type TaskStatus = "pending" | "approved" | "rejected" | "deferred";

/**
 * Server-side sort key for `GET .../recommendations` (task F4), mirroring
 * services/agent-spine/src/trax_io_spine/bff/models.py `QueueSortKey`.
 * `"priority_score"` is the default and reproduces the queue's pre-existing
 * (and only) ordering byte-for-byte.
 */
export type QueueSortKey =
  | "priority_score"
  | "estimated_cost_impact"
  | "confidence_score"
  | "criticality_tier";

export type RejectReason =
  | "wrong_for_fleet"
  | "wrong_essentiality"
  | "bad_lead_time"
  | "planner_override"
  | "other";

export interface QueueRow {
  recommendation_id: string;
  pn: string;
  location: string;
  type: RecommendationType;
  criticality_tier: number;
  aog_risk_level: AogRiskLevel;
  confidence_score: number;
  recommended_quantity: number;
  estimated_cost_impact: number;
  tier: AutonomyTier;
  priority_score: number;
  status: TaskStatus;
  reason: string;
  approvable: boolean;
  description: string;
  current_stock: number;
  shortage_quantity: number;
  recommended_location: string | null;
  horizon_days: number;
}

export interface PagedQueue {
  items: QueueRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface EvidenceView {
  kind: string;
  ref_id: string;
  detail: string;
  as_of: string | null;
}

export interface RecommendationDetail {
  recommendation_id: string;
  pn: string;
  location: string;
  type: RecommendationType;
  criticality_tier: number;
  aog_risk_level: AogRiskLevel;
  confidence_score: number;
  recommended_quantity: number;
  estimated_cost_impact: number;
  tier: AutonomyTier;
  status: TaskStatus;
  reason: string;
  provenance_id: string | null;
  projected_demand: number;
  current_policy: PolicyView | null;
  proposed_policy: PolicyView | null;
  supporting_evidence: EvidenceView[];
  guardrail_flags: string[];
  description: string;
  current_stock: number;
  shortage_quantity: number;
  recommended_location: string | null;
  horizon_days: number;
}

export interface RejectRequest {
  reason: RejectReason;
  detail?: string;
}

export interface DeferRequest {
  until?: string | null;
}

export interface BulkApproveFilter {
  tiers?: AutonomyTier[] | null;
  max_delta_pct?: number | null;
  criticality_min?: number | null;
  types?: RecommendationType[] | null;
}

export interface ActionResult {
  recommendation_id: string;
  status: TaskStatus;
  writeback: unknown | null;
  message: string;
}

export interface BulkApproveResult {
  approved_count: number;
  results: ActionResult[];
}

export interface KillSwitchState {
  engaged: boolean;
}

/**
 * Slice S5 — Forecast & Service Levels shapes, mirroring
 * services/agent-spine/src/trax_io_spine/bff/models.py (ServiceLevelBand,
 * MethodCoverageRow, AccuracyPoint, ForecastSummary et al.).
 */

export interface ServiceLevelBand {
  criticality_tier: number;
  target_service_level: number;
  sku_count: number;
  actual_coverage: number | null;
}

export interface ServiceLevelPolicy {
  bands: ServiceLevelBand[];
}

export interface MethodCoverageRow {
  regime: string;
  method: string;
  sku_count: number;
  pct: number;
}

export interface MethodCoverage {
  total_skus: number;
  rows: MethodCoverageRow[];
}

export interface AccuracyPoint {
  period_start: string;
  actual: number;
  projected: number;
}

/** `status` is always "proxy" in v1 — no backtest runs at serve time (honest gap). */
export interface ForecastAccuracy {
  status: string;
  note: string;
  points: AccuracyPoint[];
}

export interface ForecastSummary {
  service_levels: ServiceLevelPolicy;
  method_coverage: MethodCoverage;
  accuracy: ForecastAccuracy;
}

/**
 * Slice S6 — What-If Scenarios shapes, mirroring
 * services/agent-spine/src/trax_io_spine/bff/models.py (ScenarioParamsWire,
 * ScenarioSolveResult, Scenario et al.).
 */

export type ScenarioScopeKind = "all" | "criticality_tier" | "ata_chapter";

export interface ScenarioParams {
  service_level_target?: number | null;
  /**
   * Keyed by criticality tier (1-5). `Record<string, ...>` because JSON object keys
   * are always strings at runtime — the BFF's `dict[int, float]` serializes tier
   * numbers as string keys over the wire (see bff/models.py `ScenarioParamsWire`).
   */
  service_level_by_tier?: Record<string, number>;
  /**
   * Informational only: flags whether the proposed investment exceeds this cap via
   * `ScenarioSolveResult.budget_cap_binds`. Does NOT filter, scale, or otherwise
   * constrain the solve (see bff/models.py `ScenarioParamsWire.budget_cap`).
   */
  budget_cap?: number | null;
  /**
   * Legacy compatibility: this has always adjusted NEW procurement lead time.
   * It must never be interpreted as repair TAT.
   */
  lead_time_delta_pct?: number;
  procurement_lead_time_delta_pct?: number | null;
  repair_tat_delta_pct?: number;
  scope?: ScenarioScopeKind;
  scope_value?: string | null;
}

/**
 * `projected_coverage` is the target cycle-service-level a fully-funded proposed
 * policy would achieve (monotonic in the SL slider). `on_hand_gap_ratio` is the
 * fraction of scoped keys whose current real on-hand already meets the proposed
 * reorder point — real, useful, but NOT expected to be monotonic in SL (see
 * services/agent-spine/src/trax_io_spine/bff/scenario.py module docstring).
 */
export interface ScenarioOutcome {
  service_level: number;
  projected_investment: number;
  projected_coverage: number;
  on_hand_gap_ratio: number;
  scored_keys: number;
}

export interface FrontierPoint {
  service_level: number;
  projected_investment: number;
  projected_coverage: number;
}

export interface ScenarioRepairReturnOutcome {
  horizon_days: number;
  eligible_quantity: number;
  expected_units: number;
  modeled_keys: number;
  unavailable_keys: number;
  /** Eligible repair keys omitted because the selected scope metadata was absent. */
  unscoped_keys?: number;
  serviceable_yield_assumption: number;
}

export interface ScenarioAssumptionImpact {
  label: string;
  affected_key_count: number;
}

export interface ScenarioSolveResult {
  params: Required<Pick<ScenarioParams, "lead_time_delta_pct" | "scope">> & ScenarioParams;
  current: ScenarioOutcome;
  proposed: ScenarioOutcome;
  delta_investment: number;
  delta_coverage: number;
  frontier: FrontierPoint[];
  skipped_keys: number;
  total_keys: number;
  budget_cap_binds: boolean;
  /** Additive Phase 6 metadata; old saved scenarios may omit every field. */
  contract_version?: "scenario-solve.v1" | "scenario-solve.v2";
  repair_current?: ScenarioRepairReturnOutcome | null;
  repair_proposed?: ScenarioRepairReturnOutcome | null;
  assumption_impacts?: ScenarioAssumptionImpact[];
  affected_key_count?: number | null;
  fingerprint?: string | null;
  /** Source-owned scenario provenance. Legacy saved results omit these fields. */
  source_as_of?: string | null;
  source_coverage?: number | null;
  source_confidence?: number | null;
  warning_codes?: string[];
}

export type ScenarioStatus = "draft" | "committed";

export interface Scenario {
  id: string;
  name: string;
  params: ScenarioSolveResult["params"];
  result: ScenarioSolveResult;
  status: ScenarioStatus;
  created_at: string;
  committed_at: string | null;
}

export interface SaveScenarioRequest {
  name: string;
  params: ScenarioParams;
  result: ScenarioSolveResult;
}

/** Commit acknowledgement — NOT a real eMRO writeback (see bff/models.py docstring). */
export interface ScenarioAuditEvent {
  scenario_id: string;
  scenario_name: string;
  action: "commit";
  at: string;
  note: string;
}

/**
 * Writeback history and rollback shapes.
 */

/** WritebackStatus — mirror of trax_io_spine.contracts.WritebackStatus. */
export type WritebackStatus = "written" | "deferred_open_order" | "failed" | "shadowed";

/** RollbackStatus — mirror of trax_io_spine.contracts.RollbackStatus. */
export type RollbackStatus = "rolled_back" | "outside_window" | "nothing_to_revert";

/**
 * One writeback-ledger entry for a (pn, location), mirroring
 * trax_io_spine.contracts.HistoryEntry. Audit event — rendered as a timeline
 * row, NOT a MetricValue (carries its own provenance_id/changed_by inline).
 */
export interface HistoryEntry {
  tenant_id: string;
  pn: string;
  location: string;
  version: number;
  status: WritebackStatus;
  old_values: Record<string, number> | null;
  new_values: Record<string, number>;
  provenance_id: string;
  tier: AutonomyTier | null;
  agent_version: string;
  changed_by_principal: string;
  idempotency_key: string | null;
  parent_version: number | null;
  changed_at: string;
}

/** RollbackRequest — mirror of trax_io_spine.contracts.RollbackRequest. */
export interface RollbackRequest {
  tenant_id: string;
  pn: string;
  location: string;
  reason: string;
  principal: string;
  requested_at: string;
}

/** RollbackResult — mirror of trax_io_spine.contracts.RollbackResult. */
export interface RollbackResult {
  tenant_id: string;
  pn: string;
  location: string;
  status: RollbackStatus;
  from_values: Record<string, number> | null;
  to_values: Record<string, number> | null;
  reverted_from_version: number | null;
  new_version: number | null;
  rolled_back_at: string | null;
  error_message: string | null;
}

/**
 * Slice S7 — Data & Connections / feed health shapes, mirroring
 * services/agent-spine/src/trax_io_spine/bff/models.py (FeedId, FeedHealthRow,
 * FeedHealthStrip, FeedsSummary) and the code-verified mapping in bff/feeds.py.
 */

/** The 13 canonical feeds (DATA-MODEL.md §2), in spec order. */
export type FeedId =
  | "REQUISITIONS"
  | "PURCHASE_ORDERS"
  | "QUOTATIONS"
  | "REPAIR_ORDERS"
  | "INVENTORY"
  | "SERIAL_TRACKING"
  | "RELIABILITY"
  | "FLEET_UTILIZATION"
  | "MAINTENANCE_SCHEDULE"
  | "VENDOR_MASTER"
  | "INTERCHANGEABILITY"
  | "CONTRACTS"
  | "SHELF_LIFE";

/**
 * Truthful connection status derived from the real nightly-extract domain
 * registry and what the recommendation-engine's extract_loader actually
 * consumes — NOT the spec's data-quality FeedHealth.status. "connected" means
 * extracted AND consumed; "partial" means extracted-but-unconsumed or
 * structurally thin; "not_connected" means no eMRO domain is wired at all.
 */
export type FeedConnectionStatus = "connected" | "partial" | "not_connected";

export interface FeedHealthRow {
  feed_id: FeedId;
  name: string;
  status: FeedConnectionStatus;
  /** Real extract domain names (domains.py) backing this feed; empty when not_connected. */
  domains: string[];
  /** Row count from the manifest artifact, when present — null, never fabricated. */
  rows: number | null;
  /** The extract's manifest extract_date, when at least one backing domain ran. */
  last_sync: string | null;
  /** Honest caveat: what's collapsed, what's unwired, what has no eMRO source at all. */
  notes: string;
}

export interface FeedHealthStrip {
  connected: number;
  partial: number;
  not_connected: number;
  extract_date: string | null;
}

export interface FeedsSummary {
  health: FeedHealthStrip;
  feeds: FeedHealthRow[];
}

// Business Value Report (BVR) — TS mirrors of trax_io_spine.bvr models.
export interface ProjectedComponent {
  name: string;
  amount: string; // Decimal serialized as string by the BFF
  formula: string;
  inputs: Record<string, number>;
  assumptions: string[];
}

export interface BvrSavings {
  holding_cost_delta: ProjectedComponent;
  ordering_cost_delta: ProjectedComponent;
  stockout_risk_delta: ProjectedComponent;
  total_projected_applied: string;
  total_projected_shadowed: string;
  total_projected: string;
  changes_total: number;
  changes_valued: number;
  assumption_rates: Record<string, number>;
}

export interface TierPosture {
  tier: number;
  target_fill_rate: number;
  keys: number;
  keys_at_posture: number;
  posture_rate: number;
}

export interface BvrGovernance {
  recommendations_total: number;
  pending: number;
  approved: number;
  rejected: number;
  deferred: number;
  approval_rate: number;
  override_rate: number;
  writes_written: number;
  writes_shadowed: number;
  writes_failed: number;
  writes_deferred_open_order: number;
  rollbacks: number;
  tier_mix: Record<string, number>;
  kill_switch_engaged: boolean;
}

export interface BvrReport {
  schema_version: string;
  tenant_id: string;
  period: {
    extract_date: string | null;
    decision_window_start: string | null;
    decision_window_end: string | null;
    generated_at: string;
    label: string;
  };
  executive_summary: {
    total_projected: string;
    changes_applied: number;
    changes_shadowed: number;
    keys_under_management: number;
    open_pipeline_value: string;
    service_headline: string;
  };
  savings: BvrSavings;
  service_posture: { tiers: TierPosture[]; note: string };
  governance: BvrGovernance;
  forward_look: {
    open_pipeline_value: string;
    projected_demand_horizon: number;
    top_opportunities: {
      pn: string;
      location: string;
      type: string;
      estimated_cost_impact: string;
    }[];
  };
  methodology: {
    formulas: string[];
    assumption_rates: Record<string, number>;
    ledger_entries: number;
    recommendations: number;
    keys: number;
    keys_total_portfolio: number;
    input_snapshot_hashes: string[];
    input_snapshot_hash_count: number;
    agent_version: string;
    generated_by: string;
  };
}
