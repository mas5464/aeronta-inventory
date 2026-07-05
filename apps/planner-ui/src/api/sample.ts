import type {
  BvrReport,
  DashboardSummary,
  DemandPoint,
  HistoryEntry,
  PartContext,
  QueueRow,
  RecommendationDetail,
} from "./types";

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
      description: "Hydraulic pump",
      current_stock: 4,
      shortage_quantity: 3,
      recommended_location: "YOW",
      horizon_days: 90,
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
      guardrail_flags: ["active_aog"],
      guardrail_notes: ["An aircraft is currently AOG for this part — routed for immediate review."],
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
      description: "Hydraulic pump",
      current_stock: 2,
      shortage_quantity: 1,
      recommended_location: "YYZ",
      horizon_days: 90,
    },
    {
      provenance_id: "prov-91bd",
      projected_demand: 0.31,
      current_policy: { rop: 4, eoq: 6, safety_stock: 1, max_stock: 12 },
      proposed_policy: { rop: 6, eoq: 8, safety_stock: 2, max_stock: 16 },
      supporting_evidence: [
        { kind: "demand_history", ref_id: "DH", detail: "9 removals / 24mo", as_of: null },
      ],
      guardrail_flags: [],
      guardrail_notes: [],
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
      description: "Cabin air filter",
      current_stock: 18,
      shortage_quantity: 0,
      recommended_location: null,
      horizon_days: 60,
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
      guardrail_notes: [],
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
      description: "Modulating valve",
      current_stock: 25,
      shortage_quantity: 0,
      recommended_location: null,
      horizon_days: 120,
    },
    {
      provenance_id: null,
      projected_demand: 0.9,
      current_policy: { rop: 8, eoq: 20, safety_stock: 3, max_stock: 40 },
      proposed_policy: null,
      supporting_evidence: [],
      guardrail_flags: [],
      guardrail_notes: [],
    },
  ),
];

// Portfolio-level summary for the dashboard view (Task B3). Hand-built but
// internally consistent with SAMPLE_SEED: 4 parts, total_on_hand/shortage/
// net_cost_impact sum the seed rows' current_stock/shortage_quantity/
// estimated_cost_impact, aog_exposure counts rows with aog_risk_level >= 3
// (only rec-hyd-yyz), and top_shortages is sorted by shortage desc.
export const SAMPLE_DASHBOARD: DashboardSummary = {
  parts: 4,
  total_on_hand: 49, // 4 + 2 + 18 + 25
  total_on_hand_value: 137_200, // rough on-hand qty * unit costs across the portfolio
  total_shortage: 4, // 3 + 1 + 0 + 0
  total_projected_demand: 2.73, // 0.42 + 0.31 + 1.1 + 0.9
  aog_exposure: 1, // rec-hyd-yyz is the only row with aog_risk_level >= 3
  open_recommendations: 4,
  net_cost_impact: 12_980, // 8400 + 5600 + 180 - 1200
  by_criticality: [
    { key: "1", count: 2, on_hand: 6, shortage: 4 },
    { key: "3", count: 1, on_hand: 18, shortage: 0 },
    { key: "4", count: 1, on_hand: 25, shortage: 0 },
  ],
  by_ata: [
    { key: "29", count: 2, on_hand: 6, shortage: 4 },
    { key: "21", count: 1, on_hand: 18, shortage: 0 },
    { key: "27", count: 1, on_hand: 25, shortage: 0 },
  ],
  by_part_class: [
    { key: "rotable", count: 2, on_hand: 6, shortage: 4 },
    { key: "expendable", count: 1, on_hand: 18, shortage: 0 },
    { key: "repairable", count: 1, on_hand: 25, shortage: 0 },
  ],
  by_tier: [
    { key: "1", count: 2, on_hand: 6, shortage: 4 },
    { key: "2", count: 1, on_hand: 18, shortage: 0 },
    { key: "3", count: 1, on_hand: 25, shortage: 0 },
  ],
  top_shortages: [
    { pn: "HYD-PUMP-001", location: "YYZ", shortage: 3, on_hand: 4, projected_demand: 0.42 },
    { pn: "HYD-PUMP-001", location: "YOW", shortage: 1, on_hand: 2, projected_demand: 0.31 },
  ],
};

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

// A ~12-point monthly demand series (removals + issues) so DemandTrend has data
// to plot in offline dev / tests. Values loosely echo a slow-moving rotable.
function sampleDemandPoints(): DemandPoint[] {
  const monthly: Array<[number, number]> = [
    [1, 0], [0, 1], [2, 0], [1, 1], [0, 0], [1, 0],
    [2, 1], [0, 1], [1, 0], [1, 1], [0, 1], [2, 0],
  ];
  return monthly.map(([removals, issues], i) => {
    const month = i + 1;
    return {
      period_start: `2025-${String(month).padStart(2, "0")}-01`,
      removals,
      issues,
      total: removals + issues,
    };
  });
}

// Realistic PartContext for the fake client (Task C3) — description, stock
// breakdown, lead time, one open order, and a populated demand series so
// DemandTrend has something to render.
export function SAMPLE_PART_CONTEXT(pn: string, location: string): PartContext {
  const points = sampleDemandPoints();
  return {
    pn,
    location,
    attributes: {
      description: "Hydraulic pump",
      ata_chapter: "29",
      part_class: "rotable",
      shelf_life_days: null,
      hazardous_material: false,
      tool_control_item: false,
      criticality_tier: 1,
    },
    stock: {
      on_hand: 4,
      serviceable: 3,
      in_repair: 1,
      allocated: 0,
      rental: 0,
      loan: 0,
    },
    current_policy: { rop: 6, eoq: 10, safety_stock: 2, max_stock: 20 },
    proposed_policy: { rop: 9, eoq: 12, safety_stock: 4, max_stock: 24 },
    lead_time: {
      promised_days: 21,
      realized_mean_days: 26.5,
      n_observations: 6,
    },
    open_orders: [
      {
        order_id: "PO-4471",
        order_type: "purchase",
        vendor: "Trax Spares Co.",
        qty_open: 3,
        expected_rcv_date: "2026-08-04",
      },
    ],
    total_open_qty: 3,
    demand: {
      total_24mo: points.reduce((sum, p) => sum + p.total, 0),
      points,
    },
    unit_cost: 2800,
  };
}

// Business Value Report (BVR) sample for the Reports section (Task 8). Loosely
// consistent with SAMPLE_SEED (4 keys under management, 1 approved change so far).
export const SAMPLE_BVR: BvrReport = {
  schema_version: "1.1.0",
  tenant_id: "acme",
  period: {
    extract_date: "2026-04-01",
    decision_window_start: "2026-04-01T09:00:00+00:00",
    decision_window_end: "2026-04-01T09:00:00+00:00",
    generated_at: "2026-04-01T10:00:00+00:00",
    label: "Snapshot 2026-04-01",
  },
  executive_summary: {
    total_projected: "51.39",
    changes_applied: 1,
    changes_shadowed: 0,
    keys_under_management: 4,
    open_pipeline_value: "1250.00",
    service_headline: "2/3 tiers at target posture",
  },
  savings: {
    holding_cost_delta: {
      name: "holding_cost_delta",
      amount: "-14.58",
      formula: "Δ(safety_stock + EOQ/2) × unit_cost × holding_cost_rate × period_fraction",
      inputs: { changes_valued: 1, changes_total: 1 },
      assumptions: ["holding_cost_rate=0.25"],
    },
    ordering_cost_delta: {
      name: "ordering_cost_delta",
      amount: "64.64",
      formula: "(annual_demand/EOQ_old − annual_demand/EOQ_new) × per_order_cost × period_fraction",
      inputs: { changes_valued: 1, changes_total: 1 },
      assumptions: ["per_order_cost=85.0"],
    },
    stockout_risk_delta: {
      name: "stockout_risk_delta",
      amount: "1.33",
      formula: "Δ(lead-time demand covered at ROP) × unit_cost × proxy × tier_weight × period_fraction",
      inputs: { changes_valued: 1, changes_total: 1 },
      assumptions: ["stockout_proxy_fraction=0.10"],
    },
    total_projected_applied: "51.39",
    total_projected_shadowed: "0.00",
    total_projected: "51.39",
    changes_total: 1,
    changes_valued: 1,
    assumption_rates: { holding_cost_rate: 0.25, per_order_cost: 85, stockout_proxy_fraction: 0.1 },
  },
  service_posture: {
    tiers: [
      { tier: 1, target_fill_rate: 0.995, keys: 1, keys_at_posture: 1, posture_rate: 1 },
      { tier: 3, target_fill_rate: 0.95, keys: 2, keys_at_posture: 1, posture_rate: 0.5 },
    ],
    note: "Posture (ROP covers mean lead-time demand), not realized fill rate.",
  },
  governance: {
    recommendations_total: 4,
    pending: 3,
    approved: 1,
    rejected: 0,
    deferred: 0,
    approval_rate: 1,
    override_rate: 0,
    writes_written: 1,
    writes_shadowed: 0,
    writes_failed: 0,
    writes_deferred_open_order: 0,
    rollbacks: 0,
    tier_mix: { A: 0, B: 1, C: 0 },
    kill_switch_engaged: false,
  },
  forward_look: {
    open_pipeline_value: "1250.00",
    projected_demand_horizon: 18.5,
    top_opportunities: [
      { pn: "HYD-PUMP-001", location: "YYZ", type: "transfer", estimated_cost_impact: "850.00" },
    ],
  },
  methodology: {
    formulas: ["holding: Δ(ss + EOQ/2) × unit_cost × 0.25/yr × 1/12"],
    assumption_rates: { holding_cost_rate: 0.25 },
    ledger_entries: 1,
    recommendations: 4,
    keys: 4,
    keys_total_portfolio: 4,
    input_snapshot_hashes: ["sample"],
    input_snapshot_hash_count: 1,
    agent_version: "spine-0.1.0",
    generated_by: "trax_io_spine.bvr",
  },
};
