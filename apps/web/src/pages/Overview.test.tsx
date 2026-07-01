import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Overview } from "@/pages/Overview";
import { DEFAULT_BFF_URL } from "@/lib/api/client";
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
  by_criticality: [
    { key: "1", count: 4000, on_hand: 40000, shortage: 20 },
    { key: "2", count: 7000, on_hand: 35000, shortage: 60 },
    { key: "3", count: 5900, on_hand: 25000, shortage: 40 },
  ],
  by_ata: [
    { key: "32", count: 400, on_hand: 3000, shortage: 50 },
    { key: "21", count: 300, on_hand: 2000, shortage: 30 },
  ],
  by_part_class: [],
  by_tier: [],
  top_shortages: [
    { pn: "PN-100", location: "JFK", shortage: 20, on_hand: 5, projected_demand: 25 },
    { pn: "PN-200", location: "LAX", shortage: 15, on_hand: 2, projected_demand: 17 },
  ],
};

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Overview", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state, then the real Parts and Net cost impact KPIs with provenance", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleDashboard) }),
    );

    renderWithClient(<Overview />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);

    await waitFor(() => expect(screen.getByText("21,215")).toBeInTheDocument());
    expect(screen.getByText("Parts")).toBeInTheDocument();
    expect(screen.getByText("Net cost impact")).toBeInTheDocument();
    expect(screen.getByText("-$125,000")).toBeInTheDocument();

    // Every KPI card carries a ProvChip — the invariant made visible end-to-end.
    // 8 KPI cards render one Metric/ProvChip each.
    expect(screen.getAllByTestId("prov-chip").length).toBeGreaterThanOrEqual(8);
  });

  it("renders all KPI cards from the DashboardSummary", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleDashboard) }),
    );

    renderWithClient(<Overview />);

    await waitFor(() => expect(screen.getByText("Parts")).toBeInTheDocument());
    expect(screen.getByText("Total on-hand")).toBeInTheDocument();
    expect(screen.getByText("On-hand value")).toBeInTheDocument();
    expect(screen.getByText("Total shortage")).toBeInTheDocument();
    expect(screen.getByText("Projected demand")).toBeInTheDocument();
    expect(screen.getByText("AOG exposure")).toBeInTheDocument();
    expect(screen.getByText("Open recommendations")).toBeInTheDocument();
    expect(screen.getByText("Net cost impact")).toBeInTheDocument();
  });

  it("renders the health-mix donut, ATA risk list, and priority-actions preview from real aggregates", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleDashboard) }),
    );

    renderWithClient(<Overview />);

    await waitFor(() => expect(screen.getByText("Inventory health mix")).toBeInTheDocument());

    // Health mix donut: accessible via role=img with an aria-label summarizing slices.
    const donut = screen.getByRole("img", { name: /inventory health mix by count/i });
    expect(donut).toBeInTheDocument();
    expect(donut).toHaveAccessibleName(/Tier 1: 4000 \(24%\)/);

    // ATA risk list.
    expect(screen.getByText("Risk by ATA chapter")).toBeInTheDocument();
    expect(screen.getByText("ATA 32")).toBeInTheDocument();

    // Priority actions preview.
    expect(screen.getByText("Priority actions")).toBeInTheDocument();
    expect(screen.getByText("PN-100")).toBeInTheDocument();
    expect(screen.getByText("View all in Workbench →")).toBeInTheDocument();
  });

  it("renders the SL-vs-investment panel with an honest not-yet-connected disclosure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleDashboard) }),
    );

    renderWithClient(<Overview />);

    await waitFor(() =>
      expect(screen.getByText("Service level vs. investment")).toBeInTheDocument(),
    );
    expect(screen.getByText(/not yet connected/i)).toBeInTheDocument();
    expect(screen.getByText(/does not expose a service-level/i)).toBeInTheDocument();
  });

  it("renders an error state when the BFF call fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        json: () => Promise.resolve({}),
      }),
    );

    renderWithClient(<Overview />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/failed to load dashboard/i);
    expect(`${DEFAULT_BFF_URL}`).toBeTruthy();
  });
});
