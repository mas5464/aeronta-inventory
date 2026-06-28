import { afterEach, describe, expect, it, vi } from "vitest";
import { FakePlannerClient, HttpPlannerClient, PlannerError } from "./client";
import { SAMPLE_SEED } from "./sample";

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
