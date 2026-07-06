import { describe, expect, it } from "vitest";
import { formatPolicyValues, latestRevertibleEntry, writebackStatusLabel, writebackStatusVariant } from "@/features/part/writebackView";
import type { HistoryEntry } from "@/lib/api/types";

function entry(over: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    tenant_id: "acme", pn: "P1", location: "YYC", version: 1, status: "written",
    old_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
    new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
    provenance_id: "prov-1", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
    idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z", ...over,
  };
}

describe("formatPolicyValues", () => {
  it("formats the rop/eoq/safety_stock/max_stock keys", () => {
    expect(formatPolicyValues({ rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 }))
      .toBe("ROP 3 · EOQ 5 · SS 2 · Max 8");
  });
});

describe("writebackStatusLabel", () => {
  it("maps enum values to human labels", () => {
    expect(writebackStatusLabel("written")).toBe("Written");
    expect(writebackStatusLabel("deferred_open_order")).toBe("Deferred (open order)");
    expect(writebackStatusLabel("failed")).toBe("Failed");
    expect(writebackStatusLabel("shadowed")).toBe("Shadowed");
  });
});

describe("writebackStatusVariant", () => {
  it("maps each status to a Badge variant (color-coded; text label always accompanies it)", () => {
    expect(writebackStatusVariant("written")).toBe("good");
    expect(writebackStatusVariant("deferred_open_order")).toBe("warn");
    expect(writebackStatusVariant("failed")).toBe("bad");
    expect(writebackStatusVariant("shadowed")).toBe("default");
  });
});

describe("latestRevertibleEntry", () => {
  it("returns the latest written entry with non-null old_values", () => {
    const h = [entry({ version: 1 }), entry({ version: 2, status: "shadowed" })];
    expect(latestRevertibleEntry(h)?.version).toBe(1);
  });
  it("returns null when the latest written entry has null old_values", () => {
    expect(latestRevertibleEntry([entry({ version: 1, old_values: null })])).toBeNull();
  });
  it("returns null for empty history", () => {
    expect(latestRevertibleEntry([])).toBeNull();
  });
});
