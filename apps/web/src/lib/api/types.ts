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
