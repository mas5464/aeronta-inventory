import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_BFF_URL } from "@/lib/api/client";
import {
  createPlanningRun,
  getPlanningRunRerunConfig,
  getPlanningRunSelections,
  type CreatePlanningRunBody,
} from "@/lib/api/planningRuns";

const fullPortfolioBody: CreatePlanningRunBody = {
  scope_kind: "all_eligible",
  keys: [],
  budget: "100000",
  horizon_days: 60,
  currency: "USD",
  objective_weights: {
    shortage_reduction_weight: "1",
    aog_risk_reduction_weight: "1",
    holding_cost_penalty_weight: "0.01",
    ordering_cost_penalty_weight: "0.01",
    criticality_weights: {
      "1": "5",
      "2": "3",
      "3": "2",
      "4": "1",
      "5": "1",
    },
  },
  mandatory_floors: {},
  time_limit_seconds: 30,
  parent_run_id: null,
};

describe("planning run API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("submits bounded assumptions without serializing a full key universe", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ created: true, run: { run_id: "run-1" } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createPlanningRun(fullPortfolioBody, "tenant/a");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/tenant%2Fa/planning-runs`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(fullPortfolioBody),
      }),
    );
    const submitted = JSON.parse(
      fetchMock.mock.calls[0][1].body as string,
    ) as CreatePlanningRunBody;
    expect(submitted.scope_kind).toBe("all_eligible");
    expect(submitted.keys).toEqual([]);
    expect(JSON.stringify(submitted)).not.toContain("menus");
  });

  it("uses server-side selection paging and optional filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({ items: [], total: 0, limit: 25, offset: 50 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await getPlanningRunSelections("run/1", "acme", {
      limit: 25,
      offset: 50,
      decisionKey: "PN 1@MIA",
      selectedIsNoChange: false,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/planning-runs/run%2F1/selections?limit=25&offset=50&decision_key=PN+1%40MIA&selected_is_no_change=false`,
      expect.anything(),
    );
  });

  it("loads the bounded saved rerun resource with encoded tenant and parent ids", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          contract_version: "planning-rerun-config.v1",
        }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await getPlanningRunRerunConfig("run/1", "tenant/a");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/tenant%2Fa/planning-runs/run%2F1/rerun-config`,
      expect.anything(),
    );
  });
});
