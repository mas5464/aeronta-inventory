import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, bffClient, DEFAULT_BFF_URL } from "@/lib/api/client";
import type { DashboardSummary } from "@/lib/api/types";

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
