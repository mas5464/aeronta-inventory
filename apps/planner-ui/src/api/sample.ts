import type { HistoryEntry, QueueRow, RecommendationDetail } from "./types";

// A realistic seed mirroring the BFF's extract sample: 4 pending recommendations,
// 2 carrying a writable policy (approvable), 2 without (advisory). Used by the
// FakePlannerClient for tests and offline `npm run dev`.

interface SeedEntry {
  row: QueueRow;
  detail: RecommendationDetail;
}

function entry(
  row: QueueRow,
  detail: Omit<RecommendationDetail, keyof QueueRow> & Partial<QueueRow>,
): SeedEntry {
  return { row, detail: { ...row, ...detail } };
}

export const SAMPLE_SEED: SeedEntry[] = [
  entry(
    {
      recommendation_id: "rec-hyd-yyz",
      pn: "HYD-PUMP-001",
      location: "YYZ",
      type: "transfer",
      criticality_tier: 1,
      aog_risk_level: 3,
      confidence_score: 0.78,
      recommended_quantity: 3,
      estimated_cost_impact: 8400,
      tier: 1,
      priority_score: 45.9,
      status: "pending",
      reason: "Tier A — essentiality 1 (flight-safety). Requires planner approval.",
      approvable: true,
    },
    {
      provenance_id: "prov-7af3",
      projected_demand: 0.42,
      current_policy: { rop: 6, eoq: 10, safety_stock: 2, max_stock: 20 },
      proposed_policy: { rop: 9, eoq: 12, safety_stock: 4, max_stock: 24 },
      supporting_evidence: [
        { kind: "open_order", ref_id: "PO-4471", detail: "3 due 2026-05-04", as_of: "2026-04-01" },
        { kind: "demand_history", ref_id: "DH", detail: "14 removals / 24mo", as_of: null },
      ],
      guardrail_flags: ["tier_a_requires_approval"],
    },
  ),
  entry(
    {
      recommendation_id: "rec-hyd-yow",
      pn: "HYD-PUMP-001",
      location: "YOW",
      type: "purchase",
      criticality_tier: 1,
      aog_risk_level: 2,
      confidence_score: 0.71,
      recommended_quantity: 2,
      estimated_cost_impact: 5600,
      tier: 1,
      priority_score: 38.2,
      status: "pending",
      reason: "Tier A — essentiality 1. Requires planner approval.",
      approvable: true,
    },
    {
      provenance_id: "prov-91bd",
      projected_demand: 0.31,
      current_policy: { rop: 4, eoq: 6, safety_stock: 1, max_stock: 12 },
      proposed_policy: { rop: 6, eoq: 8, safety_stock: 2, max_stock: 16 },
      supporting_evidence: [
        { kind: "demand_history", ref_id: "DH", detail: "9 removals / 24mo", as_of: null },
      ],
      guardrail_flags: ["tier_a_requires_approval"],
    },
  ),
  entry(
    {
      recommendation_id: "rec-filter-yyz",
      pn: "FILTER-EXP-042",
      location: "YYZ",
      type: "adjust_min_max",
      criticality_tier: 3,
      aog_risk_level: 1,
      confidence_score: 0.64,
      recommended_quantity: 0,
      estimated_cost_impact: 180,
      tier: 2,
      priority_score: 12.4,
      status: "pending",
      reason: "Advisory — no writable policy change; review for min/max tuning.",
      approvable: false,
    },
    {
      provenance_id: null,
      projected_demand: 1.1,
      current_policy: { rop: 12, eoq: 30, safety_stock: 6, max_stock: 60 },
      proposed_policy: null,
      supporting_evidence: [
        { kind: "demand_history", ref_id: "DH", detail: "moderate, steady", as_of: null },
      ],
      guardrail_flags: [],
    },
  ),
  entry(
    {
      recommendation_id: "rec-valve-yyz",
      pn: "VALVE-MOD-117",
      location: "YYZ",
      type: "reduce_stock",
      criticality_tier: 4,
      aog_risk_level: 0,
      confidence_score: 0.59,
      recommended_quantity: -5,
      estimated_cost_impact: -1200,
      tier: 3,
      priority_score: 6.1,
      status: "pending",
      reason: "Advisory — overstock; no writable policy change.",
      approvable: false,
    },
    {
      provenance_id: null,
      projected_demand: 0.9,
      current_policy: { rop: 8, eoq: 20, safety_stock: 3, max_stock: 40 },
      proposed_policy: null,
      supporting_evidence: [],
      guardrail_flags: [],
    },
  ),
];

// A prior applied write for HYD-PUMP-001 @ YYZ so offline dev shows a populated
// writeback-history timeline (and a revertible latest entry) on the top row.
export const SAMPLE_HISTORY: HistoryEntry[] = [
  {
    tenant_id: "acme",
    pn: "HYD-PUMP-001",
    location: "YYZ",
    version: 1,
    status: "written",
    old_values: { rop: 5, eoq: 8, safety_stock: 1, max_stock: 16 },
    new_values: { rop: 6, eoq: 10, safety_stock: 2, max_stock: 20 },
    provenance_id: "prov-prior",
    tier: 1,
    agent_version: "fake-1",
    changed_by_principal: "agent-spine",
    idempotency_key: null,
    parent_version: null,
    changed_at: "2026-06-20T12:00:00Z",
  },
];
