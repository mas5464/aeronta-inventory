import { describe, expect, it } from "vitest";
import type { QueueRow } from "@/lib/api/types";
import {
  applyQueueFilters,
  DEFAULT_QUEUE_FILTERS,
  highConfidenceRows,
  HIGH_CONFIDENCE_THRESHOLD,
  MAX_PAGE_SIZE,
} from "@/features/workbench/queueView";

function row(overrides: Partial<QueueRow> = {}): QueueRow {
  return {
    recommendation_id: "rec-1",
    pn: "PN-1",
    location: "YYZ",
    type: "purchase",
    criticality_tier: 2,
    aog_risk_level: 0,
    confidence_score: 0.9,
    recommended_quantity: 4,
    estimated_cost_impact: -1200,
    tier: 2,
    priority_score: 50,
    status: "pending",
    reason: "test",
    approvable: true,
    description: "test part",
    current_stock: 1,
    shortage_quantity: 3,
    recommended_location: null,
    horizon_days: 90,
    ...overrides,
  };
}

describe("MAX_PAGE_SIZE (large-table / pagination strategy)", () => {
  it("is a positive, sane server-page bound (the 40k-SKU strategy is pagination, not virtualization)", () => {
    expect(MAX_PAGE_SIZE).toBeGreaterThan(0);
    expect(MAX_PAGE_SIZE).toBeLessThanOrEqual(200);
  });

  it("is exactly 200 — the documented ceiling a single rendered page must never exceed", () => {
    expect(MAX_PAGE_SIZE).toBe(200);
  });
});

describe("applyQueueFilters", () => {
  it("returns every row when filters are at their defaults", () => {
    const rows = [row({ recommendation_id: "a" }), row({ recommendation_id: "b" })];
    expect(applyQueueFilters(rows, DEFAULT_QUEUE_FILTERS)).toHaveLength(2);
  });

  it("filters by tier", () => {
    const rows = [row({ tier: 1 }), row({ tier: 2 }), row({ tier: 3 })];
    const result = applyQueueFilters(rows, { ...DEFAULT_QUEUE_FILTERS, tier: 2 });
    expect(result).toHaveLength(1);
    expect(result[0].tier).toBe(2);
  });

  it("filters by type", () => {
    const rows = [row({ type: "purchase" }), row({ type: "transfer" })];
    const result = applyQueueFilters(rows, { ...DEFAULT_QUEUE_FILTERS, type: "transfer" });
    expect(result).toHaveLength(1);
    expect(result[0].type).toBe("transfer");
  });

  it("filters to AOG risk >= 3 (High/Critical) when aogOnly is set", () => {
    const rows = [
      row({ recommendation_id: "low", aog_risk_level: 1 }),
      row({ recommendation_id: "high", aog_risk_level: 3 }),
      row({ recommendation_id: "critical", aog_risk_level: 4 }),
    ];
    const result = applyQueueFilters(rows, { ...DEFAULT_QUEUE_FILTERS, aogOnly: true });
    expect(result.map((r) => r.recommendation_id)).toEqual(["high", "critical"]);
  });

  it("combines tier + type + aogOnly filters (AND semantics)", () => {
    const rows = [
      row({ recommendation_id: "match", tier: 1, type: "purchase", aog_risk_level: 3 }),
      row({ recommendation_id: "wrong-type", tier: 1, type: "transfer", aog_risk_level: 3 }),
      row({ recommendation_id: "wrong-aog", tier: 1, type: "purchase", aog_risk_level: 0 }),
    ];
    const result = applyQueueFilters(rows, { tier: 1, type: "purchase", aogOnly: true });
    expect(result.map((r) => r.recommendation_id)).toEqual(["match"]);
  });
});

describe("highConfidenceRows", () => {
  it("returns rows at/above the default 80% confidence threshold that are also approvable", () => {
    const rows = [
      row({ recommendation_id: "high", confidence_score: 0.95, approvable: true }),
      row({ recommendation_id: "low", confidence_score: 0.4, approvable: true }),
      row({ recommendation_id: "at-threshold", confidence_score: HIGH_CONFIDENCE_THRESHOLD, approvable: true }),
    ];
    const result = highConfidenceRows(rows);
    expect(result.map((r) => r.recommendation_id)).toEqual(["high", "at-threshold"]);
  });

  it("excludes a high-confidence row that is not approvable (advisory)", () => {
    const rows = [row({ recommendation_id: "advisory", confidence_score: 0.99, approvable: false })];
    expect(highConfidenceRows(rows)).toHaveLength(0);
  });

  it("respects a custom threshold override", () => {
    const rows = [row({ confidence_score: 0.5, approvable: true })];
    expect(highConfidenceRows(rows, 0.4)).toHaveLength(1);
    expect(highConfidenceRows(rows, 0.6)).toHaveLength(0);
  });
});
