import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, bffClient, DEFAULT_BFF_URL } from "@/lib/api/client";
import type { DashboardSummary, PartContext } from "@/lib/api/types";

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
