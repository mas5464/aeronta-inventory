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
