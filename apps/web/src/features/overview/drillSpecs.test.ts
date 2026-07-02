import { describe, expect, it } from "vitest";
import { DRILL_SPECS, KPI_DRILL_MAP } from "@/features/overview/drillSpecs";
import type { DashboardSummary } from "@/lib/api/types";

/** The 4 breakdown arrays a `DashboardSummary` actually carries (types.ts). */
const BREAKDOWN_KEYS: Array<keyof Pick<
  DashboardSummary,
  "by_criticality" | "by_ata" | "by_part_class" | "by_tier"
>> = ["by_criticality", "by_ata", "by_part_class", "by_tier"];

describe("drillSpecs completeness (regression guard)", () => {
  it("covers every DashboardSummary breakdown key with at least one spec", () => {
    const coveredKeys = new Set(
      DRILL_SPECS.filter((spec) => spec.kind === "breakdown").map((spec) => spec.breakdownKey),
    );

    for (const key of BREAKDOWN_KEYS) {
      expect(coveredKeys.has(key)).toBe(true);
    }
  });

  it("gives every 'breakdown' spec a breakdownKey and every 'shortages' spec none", () => {
    for (const spec of DRILL_SPECS) {
      if (spec.kind === "breakdown") {
        expect(spec.breakdownKey).toBeDefined();
      } else {
        expect(spec.breakdownKey).toBeUndefined();
      }
    }
  });

  it("has unique spec ids", () => {
    const ids = DRILL_SPECS.map((spec) => spec.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("has a non-empty title and description for every spec", () => {
    for (const spec of DRILL_SPECS) {
      expect(spec.title.trim().length).toBeGreaterThan(0);
      expect(spec.description.trim().length).toBeGreaterThan(0);
    }
  });

  it("maps every KPI_DRILL_MAP value to a real spec id", () => {
    const specIds = new Set(DRILL_SPECS.map((spec) => spec.id));
    for (const [kpiKey, specId] of Object.entries(KPI_DRILL_MAP)) {
      expect(specIds.has(specId), `KPI_DRILL_MAP["${kpiKey}"] -> "${specId}" is not a real spec id`).toBe(
        true,
      );
    }
  });

  it("maps all 8 Overview KPI cards", () => {
    const expectedKpiKeys = [
      "parts",
      "total_on_hand",
      "on_hand_value",
      "total_shortage",
      "projected_demand",
      "aog_exposure",
      "open_recommendations",
      "net_cost_impact",
    ];
    expect(Object.keys(KPI_DRILL_MAP).sort()).toEqual(expectedKpiKeys.sort());
  });
});
