import { afterEach, describe, expect, it, vi } from "vitest";
import { FakePlannerClient, HttpPlannerClient, PlannerError } from "./client";
import { SAMPLE_SEED } from "./sample";
import type { HistoryEntry, RollbackRequest } from "./types";

const rbReq = (): RollbackRequest => ({
  tenant_id: "acme",
  pn: "HYD-PUMP-001",
  location: "YYZ",
  reason: "planner rollback",
  requested_at: new Date().toISOString(),
});

const seededWrite = (
  oldValues: Record<string, number>,
  newValues: Record<string, number>,
): HistoryEntry => ({
  tenant_id: "acme",
  pn: "HYD-PUMP-001",
  location: "YYZ",
  version: 1,
  status: "written",
  old_values: oldValues,
  new_values: newValues,
  provenance_id: "prov-seed",
  tier: 1,
  agent_version: "fake-1",
  changed_by_principal: "agent-spine",
  idempotency_key: null,
  parent_version: null,
  changed_at: new Date().toISOString(),
});

describe("FakePlannerClient", () => {
  it("returns pending rows priority-desc", async () => {
    const rows = await new FakePlannerClient(SAMPLE_SEED).getQueue("acme");
    expect(rows).toHaveLength(4);
    expect(rows.map((r) => r.priority_score)).toEqual([45.9, 38.2, 12.4, 6.1]);
  });

  it("approve removes the row from the pending queue", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    const res = await c.approve("acme", "rec-hyd-yyz");
    expect(res.status).toBe("approved");
    expect((await c.getQueue("acme")).map((r) => r.recommendation_id)).not.toContain("rec-hyd-yyz");
  });

  it("approve on a no-policy rec throws 409", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    await expect(c.approve("acme", "rec-filter-yyz")).rejects.toMatchObject({ status: 409 });
  });

  it("approve while the kill switch is engaged throws 423", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    await c.setKillSwitch("acme", true);
    await expect(c.approve("acme", "rec-hyd-yyz")).rejects.toBeInstanceOf(PlannerError);
    await expect(c.approve("acme", "rec-hyd-yyz")).rejects.toMatchObject({ status: 423 });
  });

  it("reject and defer remove the row and record status", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    expect((await c.reject("acme", "rec-hyd-yyz", "wrong_for_fleet")).status).toBe("rejected");
    expect((await c.defer("acme", "rec-hyd-yow")).status).toBe("deferred");
    const ids = (await c.getQueue("acme")).map((r) => r.recommendation_id);
    expect(ids).toEqual(["rec-filter-yyz", "rec-valve-yyz"]);
  });

  it("bulk-approve by tier approves the matching approvable rows and clears them", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    const res = await c.bulkApprove("acme", { tiers: [1] });
    expect(res.approved_count).toBe(2);
    expect(res.results.every((r) => r.status === "approved")).toBe(true);
    const ids = (await c.getQueue("acme")).map((r) => r.recommendation_id);
    expect(ids).toEqual(["rec-filter-yyz", "rec-valve-yyz"]); // only the advisory rows remain
  });

  it("bulk-approve with no filter approves only the approvable (policy-bearing) rows", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    const res = await c.bulkApprove("acme", {});
    expect(res.approved_count).toBe(2); // the 2 advisory rows are skipped
  });

  it("bulk-approve matches nothing when the filter excludes every approvable row", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    expect((await c.bulkApprove("acme", { tiers: [2, 3] })).approved_count).toBe(0);
    expect((await c.bulkApprove("acme", { criticality_min: 5 })).approved_count).toBe(0);
  });

  it("bulk-approve respects max_delta_pct (policy diff)", async () => {
    // Both approvable seed rows double their safety_stock → 100% max delta.
    const c = new FakePlannerClient(SAMPLE_SEED);
    expect((await c.bulkApprove("acme", { max_delta_pct: 30 })).approved_count).toBe(0);
    expect((await new FakePlannerClient(SAMPLE_SEED).bulkApprove("acme", { max_delta_pct: 100 }))
      .approved_count).toBe(2);
  });

  it("bulk-approve while the kill switch is engaged throws 423", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    await c.setKillSwitch("acme", true);
    await expect(c.bulkApprove("acme", {})).rejects.toMatchObject({ status: 423 });
  });

  it("approve records a written history entry (old_values null on the first agent write)", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    await c.approve("acme", "rec-hyd-yyz");
    const hist = await c.getHistory("acme", "HYD-PUMP-001", "YYZ");
    expect(hist).toHaveLength(1);
    expect(hist[0]).toMatchObject({
      version: 1,
      status: "written",
      old_values: null, // the in-memory target has no prior agent-applied value
      new_values: { rop: 9, eoq: 12, safety_stock: 4, max_stock: 24 },
    });
  });

  it("rollback of a sole first write returns nothing_to_revert (no known prior value)", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    await c.approve("acme", "rec-hyd-yyz");
    const res = await c.rollback("acme", rbReq());
    expect(res.status).toBe("nothing_to_revert");
  });

  it("rollback reverts the last written entry when a prior value is known", async () => {
    // A seeded prior write carries old_values, so the latest WRITTEN entry is revertible.
    const c = new FakePlannerClient(SAMPLE_SEED, [
      seededWrite({ rop: 6, eoq: 10, safety_stock: 2, max_stock: 20 }, { rop: 9, eoq: 12, safety_stock: 4, max_stock: 24 }),
    ]);
    const res = await c.rollback("acme", rbReq());
    expect(res.status).toBe("rolled_back");
    expect(res.to_values).toEqual({ rop: 6, eoq: 10, safety_stock: 2, max_stock: 20 });
    expect(res.reverted_from_version).toBe(1);
    const hist = await c.getHistory("acme", "HYD-PUMP-001", "YYZ");
    expect(hist).toHaveLength(2);
    expect(hist[1].new_values).toEqual({ rop: 6, eoq: 10, safety_stock: 2, max_stock: 20 });
  });

  it("rollback with no history returns nothing_to_revert", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    expect((await c.rollback("acme", rbReq())).status).toBe("nothing_to_revert");
  });
});

describe("HttpPlannerClient", () => {
  afterEach(() => vi.restoreAllMocks());

  it("parses a 200 body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify([{ recommendation_id: "x" }]), { status: 200 })),
    );
    const rows = await new HttpPlannerClient("http://bff").getQueue("acme");
    expect(rows[0].recommendation_id).toBe("x");
  });

  it("maps a 423 to a PlannerError with the status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "kill switch engaged" }), { status: 423 }),
      ),
    );
    await expect(new HttpPlannerClient("http://bff").approve("acme", "x")).rejects.toMatchObject({
      status: 423,
      message: "kill switch engaged",
    });
  });
});
