import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FakePlannerClient, type PlannerClient } from "../api/client";
import { SAMPLE_SEED } from "../api/sample";
import type { ActionResult, QueueRow, RecommendationDetail } from "../api/types";
import { usePlanner } from "./usePlanner";

// A deferred promise we can resolve from the test to control in-flight timing.
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const ROW: QueueRow = {
  recommendation_id: "rec-a",
  pn: "PN-A",
  location: "YYZ",
  type: "transfer",
  criticality_tier: 1,
  aog_risk_level: 0,
  confidence_score: 0.7,
  recommended_quantity: 1,
  estimated_cost_impact: 100,
  tier: 1,
  priority_score: 1,
  status: "pending",
  reason: "r",
  approvable: true,
  description: "Test part",
  current_stock: 5,
  shortage_quantity: 0,
  recommended_location: null,
  horizon_days: 90,
};

function detailFor(id: string): RecommendationDetail {
  return {
    recommendation_id: id,
    pn: id,
    location: "YYZ",
    type: "transfer",
    criticality_tier: 1,
    aog_risk_level: 0,
    confidence_score: 0.7,
    recommended_quantity: 1,
    estimated_cost_impact: 100,
    tier: 1,
    status: "pending",
    reason: "r",
    provenance_id: null,
    projected_demand: 0,
    current_policy: null,
    proposed_policy: null,
    supporting_evidence: [],
    guardrail_flags: [],
    guardrail_notes: [],
    description: "Test part",
    current_stock: 5,
    shortage_quantity: 0,
    recommended_location: null,
    horizon_days: 90,
  };
}

// Minimal client; individual tests override the methods they exercise.
function baseClient(over: Partial<PlannerClient> = {}): PlannerClient {
  return {
    getQueue: vi.fn(async (_t, _s, limit = 50, offset = 0) => ({
      items: [ROW],
      total: 1,
      limit,
      offset,
    })),
    getDetail: vi.fn(async (_t, id) => detailFor(id)),
    approve: vi.fn(async (_t, id): Promise<ActionResult> => ({
      recommendation_id: id,
      status: "approved",
      writeback: null,
      message: "ok",
    })),
    reject: vi.fn(async (_t, id): Promise<ActionResult> => ({
      recommendation_id: id,
      status: "rejected",
      writeback: null,
      message: "ok",
    })),
    defer: vi.fn(async (_t, id): Promise<ActionResult> => ({
      recommendation_id: id,
      status: "deferred",
      writeback: null,
      message: "ok",
    })),
    bulkApprove: vi.fn(async () => ({ approved_count: 0, results: [] })),
    getHistory: vi.fn(async () => []),
    rollback: vi.fn(async (_t, req) => ({
      tenant_id: req.tenant_id,
      pn: req.pn,
      location: req.location,
      status: "nothing_to_revert" as const,
    })),
    getKillSwitch: vi.fn(async () => ({ engaged: false })),
    setKillSwitch: vi.fn(async (_t, engaged) => ({ engaged })),
    getPartContext: vi.fn(async (_t, pn, location) => ({
      pn,
      location,
      attributes: {
        description: "Test part",
        ata_chapter: null,
        part_class: null,
        shelf_life_days: null,
        hazardous_material: false,
        tool_control_item: false,
        criticality_tier: null,
      },
      stock: null,
      current_policy: null,
      proposed_policy: null,
      lead_time: null,
      open_orders: [],
      total_open_qty: 0,
      demand: null,
      unit_cost: null,
    })),
    getDashboard: vi.fn(async () => ({
      parts: 0,
      total_on_hand: 0,
      total_on_hand_value: 0,
      total_shortage: 0,
      total_projected_demand: 0,
      aog_exposure: 0,
      open_recommendations: 0,
      net_cost_impact: 0,
      by_criticality: [],
      by_ata: [],
      by_part_class: [],
      by_tier: [],
      top_shortages: [],
    })),
    getBvr: vi.fn(async () => {
      throw new Error("getBvr not stubbed in this test");
    }),
    bvrDocumentUrl: vi.fn((tenant, kind) => `/v1/tenants/${tenant}/reports/bvr.${kind}`),
    ...over,
  };
}

async function ready(client: PlannerClient) {
  const hook = renderHook(() => usePlanner(client, "acme"));
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  return hook;
}

describe("usePlanner tabs", () => {
  it("switches between pending and decided rows and clears the selection", async () => {
    const client = new FakePlannerClient(SAMPLE_SEED.map((e) => ({ ...e })));
    const { result } = renderHook(() => usePlanner(client, "acme"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.tab).toBe("pending");
    expect(result.current.rows).toHaveLength(4);

    // Resolve one pending row, then it should surface under the decided tab.
    await act(async () => result.current.approve("rec-hyd-yyz"));
    await waitFor(() => expect(result.current.rows).toHaveLength(3));

    act(() => result.current.select("rec-hyd-yow"));
    act(() => result.current.setTab("decided"));
    await waitFor(() =>
      expect(result.current.rows.map((r) => r.recommendation_id)).toContain("rec-hyd-yyz"),
    );
    expect(result.current.rows.every((r) => r.status !== "pending")).toBe(true);
    expect(result.current.selectedId).toBeNull(); // tab switch clears selection
  });
});

describe("usePlanner paging", () => {
  it("advances the offset on nextPage and updates rows/total", async () => {
    const rowsByOffset: Record<number, QueueRow[]> = {
      0: [{ ...ROW, recommendation_id: "rec-1" }],
      2: [{ ...ROW, recommendation_id: "rec-2" }],
    };
    const getQueue = vi.fn(async (_t: string, _s?: string, limit = 2, offset = 0) => ({
      items: rowsByOffset[offset] ?? [],
      total: 4,
      limit,
      offset,
    }));
    const client = baseClient({ getQueue });
    const hook = renderHook(() => usePlanner(client, "acme", 2));
    await waitFor(() => expect(hook.result.current.loading).toBe(false));

    expect(hook.result.current.page).toBe(0);
    expect(hook.result.current.rows.map((r) => r.recommendation_id)).toEqual(["rec-1"]);

    act(() => hook.result.current.nextPage());
    await waitFor(() => expect(hook.result.current.page).toBe(1));
    await waitFor(() =>
      expect(hook.result.current.rows.map((r) => r.recommendation_id)).toEqual(["rec-2"]),
    );
    expect(getQueue).toHaveBeenCalledWith("acme", "pending", 2, 2);
  });

  it("resets to page 0 when switching tabs", async () => {
    const client = new FakePlannerClient(SAMPLE_SEED.map((e) => ({ ...e })));
    const { result } = await ready(client);

    act(() => result.current.nextPage()); // no-op: total(4) < limit(50), but page stays 0
    expect(result.current.page).toBe(0);

    act(() => result.current.setTab("decided"));
    await waitFor(() => expect(result.current.tab).toBe("decided"));
    expect(result.current.page).toBe(0);
  });
});

describe("usePlanner guards", () => {
  it("ignores a second action while one is in flight (double-submit guard)", async () => {
    const d = deferred<ActionResult>();
    const approve = vi.fn(() => d.promise);
    const client = baseClient({ approve });
    const { result } = await ready(client);

    act(() => result.current.approve("rec-a"));
    expect(result.current.busy).toBe(true);
    // Second click while the first is still resolving must be a no-op.
    act(() => result.current.approve("rec-a"));
    expect(approve).toHaveBeenCalledTimes(1);

    await act(async () => {
      d.resolve({ recommendation_id: "rec-a", status: "approved", writeback: null, message: "ok" });
    });
    await waitFor(() => expect(result.current.busy).toBe(false));
  });

  it("a stale getDetail does not overwrite a newer selection", async () => {
    const da = deferred<RecommendationDetail>();
    const db = deferred<RecommendationDetail>();
    const getDetail = vi.fn((_t: string, id: string) => (id === "a" ? da.promise : db.promise));
    const client = baseClient({ getDetail });
    const { result } = await ready(client);

    act(() => result.current.select("a"));
    act(() => result.current.select("b"));

    // Newer selection (b) resolves first, then the stale (a) resolves late.
    await act(async () => {
      db.resolve(detailFor("b"));
    });
    await act(async () => {
      da.resolve(detailFor("a"));
    });

    expect(result.current.detail?.recommendation_id).toBe("b");
  });

  it("loads the part context when a row is selected", async () => {
    const client = baseClient({
      getPartContext: vi.fn(async () => ({
        pn: "P",
        location: "L",
        attributes: {
          description: "d",
          ata_chapter: null,
          part_class: null,
          shelf_life_days: null,
          hazardous_material: false,
          tool_control_item: false,
          criticality_tier: null,
        },
        open_orders: [],
        total_open_qty: 0,
        stock: null,
        current_policy: null,
        proposed_policy: null,
        lead_time: null,
        demand: null,
        unit_cost: null,
      })),
    });
    const { result } = await ready(client);
    act(() => result.current.select("rec-a"));
    await waitFor(() => expect(result.current.partContext?.pn).toBe("P"));
  });
});

describe("usePlanner selection", () => {
  it("deselect clears the detail/history/part-context without touching rows or tab", async () => {
    const client = new FakePlannerClient(SAMPLE_SEED.map((e) => ({ ...e })));
    const { result } = await ready(client);
    act(() => result.current.select("rec-hyd-yyz"));
    await waitFor(() => expect(result.current.detail).not.toBeNull());

    act(() => result.current.deselect());
    expect(result.current.selectedId).toBeNull();
    expect(result.current.detail).toBeNull();
    expect(result.current.history).toEqual([]);
    expect(result.current.partContext).toBeNull();
    expect(result.current.rows).toHaveLength(4); // untouched
    expect(result.current.tab).toBe("pending"); // untouched
  });

  it("a deep-link selection (row not on the loaded page) still loads part context from the resolved detail", async () => {
    const getPartContext = vi.fn(async (_t: string, pn: string, location: string) => ({
      pn,
      location,
      attributes: {
        description: "d",
        ata_chapter: null,
        part_class: null,
        shelf_life_days: null,
        hazardous_material: false,
        tool_control_item: false,
        criticality_tier: null,
      },
      stock: null,
      current_policy: null,
      proposed_policy: null,
      lead_time: null,
      open_orders: [],
      total_open_qty: 0,
      demand: null,
      unit_cost: null,
    }));
    const client = baseClient({
      getQueue: vi.fn(async () => ({ items: [], total: 0, limit: 50, offset: 0 })), // row isn't loaded
      getPartContext,
    });
    const { result } = await ready(client);
    expect(result.current.rows).toHaveLength(0);

    act(() => result.current.select("rec-a")); // detailFor("rec-a") resolves to pn "rec-a", location "YYZ"
    await waitFor(() => expect(result.current.partContext?.pn).toBe("rec-a"));
    expect(getPartContext).toHaveBeenCalledWith("acme", "rec-a", "YYZ");
  });
});

describe("usePlanner bulk results", () => {
  it("stores per-item results only from bulkApprove, cleared by the next write", async () => {
    const results: ActionResult[] = [
      { recommendation_id: "rec-a", status: "approved", writeback: null, message: "written (written)" },
      {
        recommendation_id: "rec-b",
        status: "approved",
        writeback: null,
        message: "written (deferred_open_order)",
      },
    ];
    const bulkApprove = vi.fn(async () => ({ approved_count: 2, results }));
    const client = baseClient({ bulkApprove });
    const { result } = await ready(client);

    act(() => result.current.bulkApprove({}));
    await waitFor(() => expect(result.current.bulkResults).toEqual(results));

    // A subsequent single approve clears the stale bulk results.
    act(() => result.current.approve("rec-a"));
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(result.current.bulkResults).toBeNull();
  });

  it("clears bulkResults on tab switch", async () => {
    const results: ActionResult[] = [
      { recommendation_id: "rec-a", status: "approved", writeback: null, message: "ok" },
    ];
    const client = baseClient({
      bulkApprove: vi.fn(async () => ({ approved_count: 1, results })),
    });
    const { result } = await ready(client);
    act(() => result.current.bulkApprove({}));
    await waitFor(() => expect(result.current.bulkResults).toEqual(results));

    act(() => result.current.setTab("decided"));
    expect(result.current.bulkResults).toBeNull();
  });
});
