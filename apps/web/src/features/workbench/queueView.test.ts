import { describe, expect, it } from "vitest";
import type { QueueRow } from "@/lib/api/types";
import {
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
