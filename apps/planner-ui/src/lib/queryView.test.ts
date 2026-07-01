import { describe, expect, it } from "vitest";
import { SAMPLE_SEED } from "../api/sample";
import type { QueueRow } from "../api/types";
import { filterRows, queryRows, sortRows, summarize, toCsv } from "./queryView";

const ROWS: QueueRow[] = SAMPLE_SEED.map((e) => e.row);

describe("filterRows", () => {
  it("searches pn or location, case-insensitively", () => {
    expect(filterRows(ROWS, { search: "valve" }).map((r) => r.pn)).toEqual(["VALVE-MOD-117"]);
    expect(filterRows(ROWS, { search: "yow" }).map((r) => r.location)).toEqual(["YOW"]);
    expect(filterRows(ROWS, { search: "HYD" })).toHaveLength(2);
  });

  it("filters by tier, type, and minimum AOG risk", () => {
    expect(filterRows(ROWS, { tiers: [1] }).every((r) => r.tier === 1)).toBe(true);
    expect(filterRows(ROWS, { types: ["transfer"] }).map((r) => r.pn)).toEqual(["HYD-PUMP-001"]);
    // aogMin 3 keeps only rows with aog_risk_level >= 3 (the YYZ pump has 3)
    expect(filterRows(ROWS, { aogMin: 3 }).map((r) => r.location)).toEqual(["YYZ"]);
  });

  it("combines filters (AND) and returns all rows when empty", () => {
    expect(filterRows(ROWS, {})).toHaveLength(4);
    expect(filterRows(ROWS, { tiers: [1], search: "yyz" })).toHaveLength(1);
  });
});

describe("sortRows", () => {
  it("sorts by a numeric key ascending/descending without mutating input", () => {
    const asc = sortRows(ROWS, { key: "priority_score", dir: "asc" });
    expect(asc.map((r) => r.priority_score)).toEqual([6.1, 12.4, 38.2, 45.9]);
    const desc = sortRows(ROWS, { key: "priority_score", dir: "desc" });
    expect(desc.map((r) => r.priority_score)).toEqual([45.9, 38.2, 12.4, 6.1]);
    expect(ROWS.map((r) => r.priority_score)).toEqual([45.9, 38.2, 12.4, 6.1]); // input intact
  });

  it("coerces the Decimal-as-string cost impact to a number for sorting", () => {
    const asc = sortRows(ROWS, { key: "estimated_cost_impact", dir: "asc" });
    expect(asc.map((r) => Number(r.estimated_cost_impact))).toEqual([-1200, 180, 5600, 8400]);
  });

  it("sorts a string key (pn) alphabetically", () => {
    const asc = sortRows(ROWS, { key: "pn", dir: "asc" });
    expect(asc[0].pn).toBe("FILTER-EXP-042");
  });
});

describe("queryRows", () => {
  it("filters then sorts", () => {
    const out = queryRows(ROWS, { tiers: [1] }, { key: "priority_score", dir: "asc" });
    expect(out.map((r) => r.priority_score)).toEqual([38.2, 45.9]);
  });
});

describe("summarize", () => {
  it("computes queue KPIs", () => {
    const s = summarize(ROWS);
    expect(s.count).toBe(4);
    expect(s.netCost).toBe(8400 + 5600 + 180 - 1200); // 12980
    expect(s.aogRisk).toBe(1); // only the YYZ pump has aog >= 3
    expect(s.tierA).toBe(2); // two tier-1 rows
  });
});

describe("toCsv", () => {
  it("emits a header and one line per row, quoting fields", () => {
    const csv = toCsv(ROWS);
    const lines = csv.trim().split("\n");
    expect(lines).toHaveLength(5); // header + 4
    expect(lines[0]).toContain("pn");
    expect(lines[0]).toContain("estimated_cost_impact");
    expect(lines[1]).toContain("HYD-PUMP-001");
  });

  it("escapes embedded quotes/commas", () => {
    const row = { ...ROWS[0], reason: 'has, comma and "quote"' };
    const csv = toCsv([row]);
    expect(csv).toContain('"has, comma and ""quote"""');
  });
});
