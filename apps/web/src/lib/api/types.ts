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
  promised_days: number | null;
  realized_mean_days: number | null;
  n_observations: number;
}

export interface OpenOrderView {
  order_id: string;
  order_type: string;
  vendor: string | null;
  qty_open: number;
  expected_rcv_date: string | null;
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

export interface PartContext {
  pn: string;
  location: string;
  attributes: PartAttributesView;
  stock: StockBreakdown | null;
  current_policy: PolicyView | null;
  proposed_policy: PolicyView | null;
  lead_time: LeadTimeView | null;
  open_orders: OpenOrderView[];
  total_open_qty: number;
  demand: DemandSummary | null;
  unit_cost: number | null;
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
  service_level_by_tier?: Record<number, number>;
  budget_cap?: number | null;
  lead_time_delta_pct?: number;
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
