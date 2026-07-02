// TS mirrors of the Trax IO Planner BFF wire models (trax_io_spine.bff.models).
// Field names match the BFF JSON exactly so the client maps 1:1.

export type TaskStatus = "pending" | "approved" | "rejected" | "deferred";

export type RejectReason =
  | "wrong_for_fleet"
  | "wrong_essentiality"
  | "bad_lead_time"
  | "planner_override"
  | "other";

// AutonomyTier is an IntEnum on the wire: 1 = advisor (A), 2 = bounded (B), 3 = autonomous (C).
export type AutonomyTier = 1 | 2 | 3;

export interface PolicyView {
  rop: number;
  eoq: number;
  safety_stock: number;
  max_stock: number;
}

export interface EvidenceView {
  kind: string;
  ref_id: string;
  detail: string;
  as_of: string | null;
}

// AogRiskLevel is an IntEnum on the wire: 0 = none .. 4 = critical.
export type AogRiskLevel = 0 | 1 | 2 | 3 | 4;

export const AOG_LABEL: Record<AogRiskLevel, string> = {
  0: "None",
  1: "Low",
  2: "Medium",
  3: "High",
  4: "Critical",
};

export interface QueueRow {
  recommendation_id: string;
  pn: string;
  location: string;
  type: string;
  criticality_tier: number;
  aog_risk_level: AogRiskLevel;
  confidence_score: number;
  recommended_quantity: number;
  // Decimal on the server; arrives as a string (or number) — coerce with Number().
  estimated_cost_impact: number | string;
  tier: AutonomyTier;
  priority_score: number;
  status: TaskStatus;
  reason: string;
  approvable: boolean; // has a writable policy — approve writes rather than 409
  description: string;
  current_stock: number;
  shortage_quantity: number;
  recommended_location: string | null;
  horizon_days: number;
}

// Envelope returned by the paginated queue endpoint (GET …/recommendations).
// items is the current page (server sorted priority-desc, stable); total is the
// full count across all pages for the requested status filter.
export interface PagedQueue {
  items: QueueRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface RecommendationDetail {
  recommendation_id: string;
  pn: string;
  location: string;
  type: string;
  criticality_tier: number;
  aog_risk_level: AogRiskLevel;
  confidence_score: number;
  recommended_quantity: number;
  estimated_cost_impact: number | string;
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

// --------------------------------------------------------------------------- //
// Part Context (Task C1/C3) — TS mirrors of trax_io_spine.bff.models part-context
// views, backing the Planner UI's part-detail panel (stock, demand, lead time,
// open orders).
// --------------------------------------------------------------------------- //

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

export interface WritebackResult {
  tenant_id: string;
  pn: string;
  location: string;
  status: string;
  old_values: Record<string, number> | null;
  new_values: Record<string, number> | null;
  written_at: string | null;
  error_message: string | null;
}

export interface ActionResult {
  recommendation_id: string;
  status: TaskStatus;
  writeback: WritebackResult | null;
  message: string;
}

export interface KillSwitchState {
  engaged: boolean;
}

// Filter for POST /recommendations/bulk-approve. All fields optional; an omitted
// field is "no constraint". Mirrors trax_io_spine.bff.models.BulkApproveFilter.
export interface BulkApproveFilter {
  tiers?: AutonomyTier[];
  max_delta_pct?: number;
  criticality_min?: number;
  types?: string[];
}

export interface BulkApproveResult {
  approved_count: number;
  results: ActionResult[];
}

// Writeback provenance ledger — mirrors trax_io_spine.contracts.HistoryEntry / WritebackStatus.
export type WritebackStatus = "written" | "deferred_open_order" | "failed" | "shadowed";

export interface HistoryEntry {
  tenant_id: string;
  pn: string;
  location: string;
  version: number; // monotonic per (tenant, pn, location), starting at 1
  status: WritebackStatus;
  old_values: Record<string, number> | null;
  new_values: Record<string, number>;
  provenance_id: string;
  tier: AutonomyTier | null;
  agent_version: string;
  changed_by_principal: string;
  idempotency_key: string | null;
  parent_version: number | null;
  changed_at: string; // ISO 8601
}

export type RollbackStatus = "rolled_back" | "outside_window" | "nothing_to_revert";

export interface RollbackRequest {
  tenant_id: string;
  pn: string;
  location: string;
  reason: string;
  principal?: string;
  requested_at: string; // ISO 8601
}

export interface RollbackResult {
  tenant_id: string;
  pn: string;
  location: string;
  status: RollbackStatus;
  from_values?: Record<string, number> | null;
  to_values?: Record<string, number> | null;
  reverted_from_version?: number | null;
  new_version?: number | null;
  rolled_back_at?: string | null;
  error_message?: string | null;
}

export const TIER_LABEL: Record<AutonomyTier, string> = { 1: "A", 2: "B", 3: "C" };

// --------------------------------------------------------------------------- //
// Dashboard (Task B3) — TS mirrors of trax_io_spine.bff.models Breakdown /
// PartShortfall / DashboardSummary, backing the Planner UI's portfolio summary.
// --------------------------------------------------------------------------- //

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

// --------------------------------------------------------------------------- //
// Business Value Report (BVR) — TS mirrors of trax_io_spine.bvr models,
// backing the Planner UI's Reports section (#/reports).
// --------------------------------------------------------------------------- //

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
    input_snapshot_hashes: string[]; // bounded sample (capped server-side)
    input_snapshot_hash_count: number;
    agent_version: string;
    generated_by: string;
  };
}
