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

export interface QueueRow {
  recommendation_id: string;
  pn: string;
  location: string;
  type: string;
  criticality_tier: number;
  aog_risk_level: string;
  confidence_score: number;
  recommended_quantity: number;
  // Decimal on the server; arrives as a number (FastAPI) or string — coerce with Number().
  estimated_cost_impact: number | string;
  tier: AutonomyTier;
  priority_score: number;
  status: TaskStatus;
  reason: string;
}

export interface RecommendationDetail {
  recommendation_id: string;
  pn: string;
  location: string;
  type: string;
  criticality_tier: number;
  aog_risk_level: string;
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

export const TIER_LABEL: Record<AutonomyTier, string> = { 1: "A", 2: "B", 3: "C" };
