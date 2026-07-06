import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, bffClient, DEFAULT_BFF_URL, recommendationsExportUrl } from "@/lib/api/client";
import type {
  ActionResult,
  BulkApproveResult,
  BvrReport,
  DashboardSummary,
  FeedsSummary,
  ForecastSummary,
  HistoryEntry,
  KillSwitchState,
  PagedQueue,
  PartContext,
  RecommendationDetail,
  RollbackRequest,
  RollbackResult,
  Scenario,
  ScenarioAuditEvent,
  ScenarioSolveResult,
} from "@/lib/api/types";

const sampleDashboard: DashboardSummary = {
  parts: 21215,
  total_on_hand: 100000,
  total_on_hand_value: 5_000_000,
  total_shortage: 120,
  total_projected_demand: 800,
  aog_exposure: 3,
  open_recommendations: 42,
  net_cost_impact: -125_000,
  by_criticality: [],
  by_ata: [],
  by_part_class: [],
  by_tier: [],
  top_shortages: [],
};

describe("bffClient.getDashboard", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the dashboard from the BFF's tenant-scoped route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleDashboard),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getDashboard("acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/dashboard`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result).toEqual(sampleDashboard);
    expect(result.parts).toBe(21215);
  });

  it("defaults to the acme tenant when none is given", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleDashboard),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.getDashboard();

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/dashboard`,
      expect.anything(),
    );
  });

  it("throws an ApiError with status + detail on a non-OK response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "unknown tenant acme" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(bffClient.getDashboard("acme")).rejects.toThrow(ApiError);
    await expect(bffClient.getDashboard("acme")).rejects.toThrow(/unknown tenant acme/);
  });
});

const samplePartContext: PartContext = {
  pn: "19000-231-3",
  location: "YYC",
  attributes: {
    description: "WATER TANK HEATER BLANKET",
    ata_chapter: "38",
    part_class: "CONSUMABLE",
    shelf_life_days: null,
    hazardous_material: false,
    tool_control_item: false,
    criticality_tier: 2,
  },
  stock: {
    on_hand: 4,
    serviceable: 3,
    in_repair: 1,
    allocated: 0,
    rental: 0,
    loan: 0,
  },
  current_policy: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
  proposed_policy: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
  lead_time: { promised_days: 30, realized_mean_days: 34.2, n_observations: 12 },
  open_orders: [
    { order_id: "PO-1", order_type: "PURCHASE", vendor: "ACME", qty_open: 2, expected_rcv_date: "2026-08-01" },
  ],
  total_open_qty: 2,
  demand: {
    total_24mo: 18,
    points: [{ period_start: "2026-06-01", removals: 1, issues: 0, total: 1 }],
  },
  unit_cost: 245.5,
};

describe("bffClient.getPartContext", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the part context from the BFF's tenant-scoped route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(samplePartContext),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getPartContext("19000-231-3", "YYC", "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/parts/19000-231-3/YYC`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result).toEqual(samplePartContext);
    expect(result.attributes.description).toBe("WATER TANK HEATER BLANKET");
  });

  it("defaults to the acme tenant when none is given", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(samplePartContext),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.getPartContext("19000-231-3", "YYC");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/parts/19000-231-3/YYC`,
      expect.anything(),
    );
  });

  it("throws an ApiError with status + detail on a non-OK response (unknown part)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "unknown-pn/nowhere" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(bffClient.getPartContext("unknown-pn", "nowhere")).rejects.toThrow(ApiError);
    await expect(bffClient.getPartContext("unknown-pn", "nowhere")).rejects.toThrow(
      /unknown-pn\/nowhere/,
    );
  });
});

const sampleQueueRow = {
  recommendation_id: "rec-1",
  pn: "19000-231-3",
  location: "YYC",
  type: "purchase",
  criticality_tier: 2,
  aog_risk_level: 3,
  confidence_score: 0.92,
  recommended_quantity: 4,
  estimated_cost_impact: -1200,
  tier: 2,
  priority_score: 88.4,
  status: "pending",
  reason: "Projected shortage within lead time",
  approvable: true,
  description: "WATER TANK HEATER BLANKET",
  current_stock: 1,
  shortage_quantity: 3,
  recommended_location: null,
  horizon_days: 90,
} as const;

const samplePagedQueue: PagedQueue = {
  items: [sampleQueueRow as unknown as PagedQueue["items"][number]],
  total: 3483,
  limit: 50,
  offset: 0,
};

describe("bffClient.getQueue", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the paged queue with status/limit/offset query params (back-compat ≤4-arg call)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(samplePagedQueue),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getQueue("pending", 25, 50, "acme");

    // Old (pre-F4) shape is preserved byte-for-byte for status/limit/offset —
    // sort_by/sort_dir are appended with their BFF-matching defaults since
    // they're non-optional params with defaults, not omitted-when-undefined
    // like tier/type/aog_min.
    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations?status=pending&limit=25&offset=50&sort_by=priority_score&sort_dir=desc`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result.total).toBe(3483);
    expect(result.items[0].recommendation_id).toBe("rec-1");
  });

  it("defaults to pending status, limit 50, offset 0, tenant acme, sort_by priority_score desc", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(samplePagedQueue),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.getQueue();

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations?status=pending&limit=50&offset=0&sort_by=priority_score&sort_dir=desc`,
      expect.anything(),
    );
  });

  it("appends sort/tier/type/aog_min params when provided (task F4)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(samplePagedQueue),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.getQueue(
      "pending",
      25,
      0,
      "acme",
      "estimated_cost_impact",
      "asc",
      2,
      "purchase",
      3,
    );

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations?status=pending&limit=25&offset=0&sort_by=estimated_cost_impact&sort_dir=asc&tier=2&type=purchase&aog_min=3`,
      expect.anything(),
    );
  });

  it("omits tier/type/aog_min entirely when left undefined", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(samplePagedQueue),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.getQueue("pending", 25, 0, "acme", "confidence_score", "asc");

    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain("tier=");
    expect(calledUrl).not.toContain("type=");
    expect(calledUrl).not.toContain("aog_min=");
    expect(calledUrl).toContain("sort_by=confidence_score&sort_dir=asc");
  });
});

const sampleRecommendationDetail: RecommendationDetail = {
  recommendation_id: "rec-1",
  pn: "19000-231-3",
  location: "YYC",
  type: "purchase",
  criticality_tier: 2,
  aog_risk_level: 3,
  confidence_score: 0.92,
  recommended_quantity: 4,
  estimated_cost_impact: -1200,
  tier: 2,
  status: "pending",
  reason: "Projected shortage within lead time",
  provenance_id: "prov-1",
  projected_demand: 18,
  current_policy: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
  proposed_policy: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
  supporting_evidence: [
    { kind: "demand_history", ref_id: "dh-1", detail: "18 units over 24mo", as_of: null },
  ],
  guardrail_flags: [],
  description: "WATER TANK HEATER BLANKET",
  current_stock: 1,
  shortage_quantity: 3,
  recommended_location: null,
  horizon_days: 90,
};

describe("bffClient.getRecommendation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches recommendation detail by id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleRecommendationDetail),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getRecommendation("rec-1", "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations/rec-1`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result.reason).toBe("Projected shortage within lead time");
  });
});

const sampleActionResult: ActionResult = {
  recommendation_id: "rec-1",
  status: "approved",
  writeback: null,
  message: "",
};

describe("bffClient action mutations", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("approve() POSTs to .../approve", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleActionResult),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.approve("rec-1", "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations/rec-1/approve`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.status).toBe("approved");
  });

  it("approve() surfaces a 423 as an ApiError when the kill switch is engaged", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 423,
      statusText: "Locked",
      json: () => Promise.resolve({ detail: "kill switch engaged" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(bffClient.approve("rec-1", "acme")).rejects.toThrow(/kill switch engaged/);
  });

  it("reject() POSTs reason + detail to .../reject", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ...sampleActionResult, status: "rejected" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.reject("rec-1", "wrong_for_fleet", "not on this fleet", "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations/rec-1/reject`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ reason: "wrong_for_fleet", detail: "not on this fleet" }),
      }),
    );
  });

  it("defer() POSTs to .../defer", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ...sampleActionResult, status: "deferred" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.defer("rec-1", undefined, "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations/rec-1/defer`,
      expect.objectContaining({ method: "POST", body: JSON.stringify({}) }),
    );
  });
});

const sampleBulkApproveResult: BulkApproveResult = {
  approved_count: 2,
  results: [sampleActionResult, { ...sampleActionResult, recommendation_id: "rec-2" }],
};

describe("bffClient.bulkApprove", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs the filter body to .../bulk-approve", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleBulkApproveResult),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.bulkApprove({ tiers: [2, 3], criticality_min: 2 }, "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations/bulk-approve`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ tiers: [2, 3], criticality_min: 2 }),
      }),
    );
    expect(result.approved_count).toBe(2);
  });
});

describe("bffClient kill switch", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getKillSwitch() GETs .../killswitch", async () => {
    const state: KillSwitchState = { engaged: false };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(state) });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getKillSwitch("acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/killswitch`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result.engaged).toBe(false);
  });

  it("setKillSwitch() POSTs { engaged } to .../killswitch", async () => {
    const state: KillSwitchState = { engaged: true };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(state) });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.setKillSwitch(true, "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/killswitch`,
      expect.objectContaining({ method: "POST", body: JSON.stringify({ engaged: true }) }),
    );
    expect(result.engaged).toBe(true);
  });
});

const sampleForecast: ForecastSummary = {
  service_levels: {
    bands: [
      { criticality_tier: 1, target_service_level: 0.995, sku_count: 400, actual_coverage: 0.9 },
      { criticality_tier: 2, target_service_level: 0.98, sku_count: 700, actual_coverage: 0.85 },
    ],
  },
  method_coverage: {
    total_skus: 1100,
    rows: [
      { regime: "intermittent", method: "Croston/SBA/TSB", sku_count: 900, pct: 0.818 },
      {
        regime: "high_volume",
        method: "Historical + scheduled (moving average)",
        sku_count: 200,
        pct: 0.182,
      },
    ],
  },
  accuracy: {
    status: "proxy",
    note: "No backtest runs at serve time.",
    points: [{ period_start: "2026-06-01", actual: 40, projected: 35 }],
  },
};

describe("bffClient.getForecast", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the forecast summary from the BFF's tenant-scoped route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleForecast),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getForecast("acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/forecast`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result).toEqual(sampleForecast);
    expect(result.method_coverage.total_skus).toBe(1100);
    expect(result.accuracy.status).toBe("proxy");
  });

  it("defaults to the acme tenant when none is given", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleForecast),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.getForecast();

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/forecast`,
      expect.anything(),
    );
  });

  it("throws an ApiError with status + detail on a non-OK response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "unknown tenant acme" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(bffClient.getForecast("acme")).rejects.toThrow(ApiError);
    await expect(bffClient.getForecast("acme")).rejects.toThrow(/unknown tenant acme/);
  });
});

const sampleScenarioResult: ScenarioSolveResult = {
  params: { lead_time_delta_pct: 0, scope: "all", service_level_target: 0.95 },
  current: { service_level: 0.95, projected_investment: 1_000_000, projected_coverage: 0.95, on_hand_gap_ratio: 0.8, scored_keys: 21215 },
  proposed: { service_level: 0.97, projected_investment: 1_100_000, projected_coverage: 0.97, on_hand_gap_ratio: 0.75, scored_keys: 21215 },
  delta_investment: 100_000,
  delta_coverage: 0.02,
  frontier: [
    { service_level: 0.9, projected_investment: 900_000, projected_coverage: 0.9 },
    { service_level: 0.99, projected_investment: 1_300_000, projected_coverage: 0.99 },
  ],
  skipped_keys: 617,
  total_keys: 21215,
  budget_cap_binds: false,
};

describe("bffClient.solveScenario", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs the scenario params to .../scenarios/solve", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleScenarioResult),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.solveScenario({ service_level_target: 0.97 }, "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/scenarios/solve`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ service_level_target: 0.97 }),
      }),
    );
    expect(result.proposed.service_level).toBe(0.97);
    expect(result.skipped_keys).toBe(617);
  });

  it("defaults to the acme tenant when none is given", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleScenarioResult),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.solveScenario({});

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/scenarios/solve`,
      expect.anything(),
    );
  });

  it("throws an ApiError on a non-OK response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "unknown tenant ghost" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(bffClient.solveScenario({}, "ghost")).rejects.toThrow(ApiError);
  });
});

const sampleScenario: Scenario = {
  id: "scn-1",
  name: "Tier 1 to 99%",
  params: sampleScenarioResult.params,
  result: sampleScenarioResult,
  status: "draft",
  created_at: "2026-07-01T00:00:00Z",
  committed_at: null,
};

describe("bffClient scenario persistence", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("saveScenario() POSTs name/params/result to .../scenarios", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleScenario),
    });
    vi.stubGlobal("fetch", fetchMock);

    const body = { name: "Tier 1 to 99%", params: sampleScenarioResult.params, result: sampleScenarioResult };
    const result = await bffClient.saveScenario(body, "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/scenarios`,
      expect.objectContaining({ method: "POST", body: JSON.stringify(body) }),
    );
    expect(result.id).toBe("scn-1");
    expect(result.status).toBe("draft");
  });

  it("listScenarios() GETs .../scenarios", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([sampleScenario]),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.listScenarios("acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/scenarios`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("scn-1");
  });

  it("getScenario() GETs .../scenarios/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleScenario),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getScenario("scn-1", "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/scenarios/scn-1`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result.name).toBe("Tier 1 to 99%");
  });

  it("getScenario() surfaces a 404 as an ApiError for an unknown id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "scn-missing" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(bffClient.getScenario("scn-missing", "acme")).rejects.toThrow(ApiError);
  });

  it("deleteScenario() DELETEs .../scenarios/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ deleted: "scn-1" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.deleteScenario("scn-1", "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/scenarios/scn-1`,
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(result.deleted).toBe("scn-1");
  });

  it("commitScenario() POSTs .../scenarios/{id}/commit and returns an audit event", async () => {
    const auditEvent: ScenarioAuditEvent = {
      scenario_id: "scn-1",
      scenario_name: "Tier 1 to 99%",
      action: "commit",
      at: "2026-07-01T00:00:00Z",
      note: "Scenario committed as the tenant's target plan. No eMRO writeback occurred.",
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(auditEvent),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.commitScenario("scn-1", "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/scenarios/scn-1/commit`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.action).toBe("commit");
    expect(result.note).toMatch(/no eMRO writeback/i);
  });
});

const sampleFeeds: FeedsSummary = {
  health: { connected: 4, partial: 3, not_connected: 6, extract_date: "2026-04-01" },
  feeds: [
    {
      feed_id: "INVENTORY",
      name: "Current inventory / on-hand",
      status: "connected",
      domains: ["stock_amount", "stock_level_upload", "part_master"],
      rows: null,
      last_sync: "2026-04-01",
      notes: "Strongest feed in v1.",
    },
    {
      feed_id: "REPAIR_ORDERS",
      name: "Repair orders (units in shop)",
      status: "not_connected",
      domains: [],
      rows: null,
      last_sync: null,
      notes: "No dedicated repair-shop-order domain.",
    },
  ],
};

describe("bffClient.getFeeds", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the feeds summary from the BFF's tenant-scoped route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleFeeds),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getFeeds("acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/feeds`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result).toEqual(sampleFeeds);
    expect(result.health.connected).toBe(4);
    expect(result.feeds).toHaveLength(2);
  });

  it("defaults to the acme tenant when none is given", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(sampleFeeds),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.getFeeds();

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/feeds`,
      expect.anything(),
    );
  });

  it("throws an ApiError with status + detail on a non-OK response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "unknown tenant acme" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(bffClient.getFeeds("acme")).rejects.toThrow(ApiError);
    await expect(bffClient.getFeeds("acme")).rejects.toThrow(/unknown tenant acme/);
  });
});

describe("recommendationsExportUrl", () => {
  it("builds the export URL with default status/sort, omitting tier/type/aog", () => {
    const url = recommendationsExportUrl({}, "acme");
    expect(url).toBe(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations/export.csv?status=pending&sort_by=priority_score&sort_dir=desc`,
    );
  });

  it("defaults to the acme tenant when none is given", () => {
    expect(recommendationsExportUrl()).toContain("/v1/tenants/acme/recommendations/export.csv");
  });

  it("appends tier/type/aog_min when provided", () => {
    const url = recommendationsExportUrl(
      { status: "pending", sortBy: "estimated_cost_impact", sortDir: "asc", tier: 2, type: "purchase", aogMin: 3 },
      "acme",
    );
    expect(url).toBe(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations/export.csv?status=pending&sort_by=estimated_cost_impact&sort_dir=asc&tier=2&type=purchase&aog_min=3`,
    );
  });

  it("omits tier/type/aog_min entirely when undefined", () => {
    const url = recommendationsExportUrl({ status: "pending" }, "acme");
    expect(url).not.toContain("tier=");
    expect(url).not.toContain("type=");
    expect(url).not.toContain("aog_min=");
  });
});

const sampleHistory: HistoryEntry[] = [
  {
    tenant_id: "acme", pn: "19000-231-3", location: "YYC", version: 1,
    status: "written", old_values: null, new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
    provenance_id: "prov-1", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
    idempotency_key: "k1", parent_version: null, changed_at: "2026-06-20T00:00:00Z",
  },
];

describe("bffClient.getHistory", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs the (pn,location)-scoped history route with URL-encoded query params", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleHistory) });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getHistory("19000-231-3", "YYC", "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/history?pn=19000-231-3&location=YYC`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result[0].new_values.rop).toBe(3);
  });

  it("URL-encodes pn/location containing special characters", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) });
    vi.stubGlobal("fetch", fetchMock);
    await bffClient.getHistory("A/B 1", "Y Z", "acme");
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("pn=A%2FB+1");
    expect(url).toContain("location=Y+Z");
  });

  it("throws an ApiError on a non-OK response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 404, statusText: "Not Found", json: () => Promise.resolve({ detail: "unknown tenant ghost" }),
    }));
    await expect(bffClient.getHistory("p", "l", "ghost")).rejects.toThrow(ApiError);
  });
});

describe("bffClient.rollback", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("POSTs the RollbackRequest body to .../rollback", async () => {
    const rollbackResult: RollbackResult = {
      tenant_id: "acme", pn: "19000-231-3", location: "YYC", status: "rolled_back",
      from_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
      to_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
      reverted_from_version: 1, new_version: 2, rolled_back_at: "2026-07-06T00:00:00Z", error_message: null,
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(rollbackResult) });
    vi.stubGlobal("fetch", fetchMock);

    const req: RollbackRequest = {
      tenant_id: "acme", pn: "19000-231-3", location: "YYC",
      reason: "wrong policy", principal: "planner", requested_at: "2026-07-06T00:00:00Z",
    };
    const result = await bffClient.rollback(req, "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/rollback`,
      expect.objectContaining({ method: "POST", body: JSON.stringify(req) }),
    );
    expect(result.status).toBe("rolled_back");
    expect(result.new_version).toBe(2);
  });
});

const sampleBvr: BvrReport = {
  schema_version: "1.1.0",
  tenant_id: "acme",
  period: { extract_date: "2026-04-01", decision_window_start: null, decision_window_end: null, generated_at: "2026-07-06T00:00:00Z", label: "As of 2026-04-01" },
  executive_summary: { total_projected: "1250.00", changes_applied: 3, changes_shadowed: 1, keys_under_management: 57605, open_pipeline_value: "42000.00", service_headline: "0/5 tiers at target posture" },
  savings: {
    holding_cost_delta: { name: "holding_cost_delta", amount: "-0.06", formula: "Δ carrying cost", inputs: {}, assumptions: [] },
    ordering_cost_delta: { name: "ordering_cost_delta", amount: "0.00", formula: "Δ ordering cost", inputs: {}, assumptions: [] },
    stockout_risk_delta: { name: "stockout_risk_delta", amount: "0.00", formula: "Δ stockout risk", inputs: {}, assumptions: [] },
    total_projected_applied: "1250.00", total_projected_shadowed: "0.00", total_projected: "1250.00",
    changes_total: 4, changes_valued: 3, assumption_rates: { holding: 0.2 },
  },
  service_posture: { tiers: [], note: "Posture note" },
  governance: {
    recommendations_total: 4, pending: 2, approved: 1, rejected: 1, deferred: 0,
    approval_rate: 0.5, override_rate: 0.25, writes_written: 1, writes_shadowed: 0,
    writes_failed: 0, writes_deferred_open_order: 0, rollbacks: 0, tier_mix: { "1": 2 }, kill_switch_engaged: false,
  },
  forward_look: { open_pipeline_value: "42000.00", projected_demand_horizon: 90, top_opportunities: [{ pn: "P1", location: "YYC", type: "purchase", estimated_cost_impact: "8400.00" }] },
  methodology: { formulas: ["holding = ..."], assumption_rates: { holding: 0.2 }, ledger_entries: 1, recommendations: 4, keys: 57605, keys_total_portfolio: 58899, input_snapshot_hashes: ["abc"], input_snapshot_hash_count: 1, agent_version: "agent-spine-v1", generated_by: "bvr" },
};

describe("bffClient.getBvr", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("fetches the BVR from the tenant-scoped route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleBvr) });
    vi.stubGlobal("fetch", fetchMock);
    const result = await bffClient.getBvr("acme");
    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/reports/bvr`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result.schema_version).toBe("1.1.0");
    expect(result.savings.holding_cost_delta.name).toBe("holding_cost_delta");
  });

  it("defaults to the acme tenant", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleBvr) });
    vi.stubGlobal("fetch", fetchMock);
    await bffClient.getBvr();
    expect(fetchMock).toHaveBeenCalledWith(`${DEFAULT_BFF_URL}/v1/tenants/acme/reports/bvr`, expect.anything());
  });

  it("throws an ApiError on a non-OK response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Server Error", json: () => Promise.resolve({ detail: "failed to build BVR report" }) }));
    await expect(bffClient.getBvr("acme")).rejects.toThrow(ApiError);
  });
});

describe("bffClient.bvrDocumentUrl", () => {
  it("builds the html and pdf document URLs", () => {
    expect(bffClient.bvrDocumentUrl("acme", "html")).toBe(`${DEFAULT_BFF_URL}/v1/tenants/acme/reports/bvr.html`);
    expect(bffClient.bvrDocumentUrl("acme", "pdf")).toBe(`${DEFAULT_BFF_URL}/v1/tenants/acme/reports/bvr.pdf`);
  });

  it("defaults to the acme tenant", () => {
    expect(bffClient.bvrDocumentUrl(undefined, "html")).toContain("/v1/tenants/acme/reports/bvr.html");
  });
});
