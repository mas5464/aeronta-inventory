// Pure client-side view layer over the recommendation queue: search, filter, sort,
// KPI summary, and CSV export. No I/O — trivially testable, reused by the toolbar,
// summary cards, and table.

import type { AogRiskLevel, AutonomyTier, QueueRow } from "../api/types";

export interface QueueFilter {
  search?: string;
  tiers?: AutonomyTier[];
  types?: string[];
  aogMin?: AogRiskLevel; // keep rows with aog_risk_level >= aogMin
}

export type SortKey =
  | "pn"
  | "type"
  | "tier"
  | "criticality_tier"
  | "aog_risk_level"
  | "confidence_score"
  | "estimated_cost_impact"
  | "priority_score";

export interface SortSpec {
  key: SortKey;
  dir: "asc" | "desc";
}

export function filterRows(rows: QueueRow[], f: QueueFilter): QueueRow[] {
  const q = f.search?.trim().toLowerCase();
  return rows.filter((r) => {
    if (q && !`${r.pn} ${r.location}`.toLowerCase().includes(q)) return false;
    if (f.tiers && f.tiers.length > 0 && !f.tiers.includes(r.tier)) return false;
    if (f.types && f.types.length > 0 && !f.types.includes(r.type)) return false;
    if (f.aogMin != null && r.aog_risk_level < f.aogMin) return false;
    return true;
  });
}

function sortValue(row: QueueRow, key: SortKey): number | string {
  const v = row[key];
  if (key === "pn" || key === "type") return String(v);
  return Number(v); // handles estimated_cost_impact arriving as a Decimal string
}

export function sortRows(rows: QueueRow[], sort: SortSpec): QueueRow[] {
  const factor = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = sortValue(a, sort.key);
    const bv = sortValue(b, sort.key);
    if (av < bv) return -1 * factor;
    if (av > bv) return 1 * factor;
    return 0;
  });
}

export function queryRows(rows: QueueRow[], f: QueueFilter, sort: SortSpec): QueueRow[] {
  return sortRows(filterRows(rows, f), sort);
}

export interface QueueSummary {
  count: number;
  netCost: number;
  aogRisk: number; // rows with aog_risk_level >= 3 (high/critical)
  tierA: number; // rows requiring approval (tier A)
}

export function summarize(rows: QueueRow[]): QueueSummary {
  return {
    count: rows.length,
    netCost: rows.reduce((sum, r) => sum + Number(r.estimated_cost_impact), 0),
    aogRisk: rows.filter((r) => r.aog_risk_level >= 3).length,
    tierA: rows.filter((r) => r.tier === 1).length,
  };
}

const CSV_COLUMNS: (keyof QueueRow)[] = [
  "recommendation_id",
  "pn",
  "location",
  "type",
  "tier",
  "criticality_tier",
  "aog_risk_level",
  "confidence_score",
  "recommended_quantity",
  "estimated_cost_impact",
  "priority_score",
  "status",
  "reason",
];

function csvCell(value: unknown): string {
  const s = String(value ?? "");
  return `"${s.replace(/"/g, '""')}"`;
}

export function toCsv(rows: QueueRow[]): string {
  const header = CSV_COLUMNS.join(",");
  const lines = rows.map((r) => CSV_COLUMNS.map((c) => csvCell(r[c])).join(","));
  return [header, ...lines].join("\n") + "\n";
}
