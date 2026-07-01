import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, bffClient, DEFAULT_BFF_URL } from "@/lib/api/client";
import type {
  ActionResult,
  BulkApproveResult,
  DashboardSummary,
  FeedsSummary,
  ForecastSummary,
  KillSwitchState,
  PagedQueue,
  PartContext,
  RecommendationDetail,
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

  it("fetches the paged queue with status/limit/offset query params", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(samplePagedQueue),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getQueue("pending", 25, 50, "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations?status=pending&limit=25&offset=50`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result.total).toBe(3483);
    expect(result.items[0].recommendation_id).toBe("rec-1");
  });

  it("defaults to pending status, limit 50, offset 0, tenant acme", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(samplePagedQueue),
    });
    vi.stubGlobal("fetch", fetchMock);

    await bffClient.getQueue();

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations?status=pending&limit=50&offset=0`,
      expect.anything(),
    );
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
