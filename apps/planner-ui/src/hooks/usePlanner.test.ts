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
  };
}

// Minimal client; individual tests override the methods they exercise.
function baseClient(over: Partial<PlannerClient> = {}): PlannerClient {
  return {
    getQueue: vi.fn(async () => [ROW]),
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
});
