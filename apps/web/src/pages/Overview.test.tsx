import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  // 10 entries (not 8) so the ATA-risk drill panel test can assert it shows
  // MORE rows than the card's top-8 `AtaRiskList` preview.
  by_ata: [
    { key: "32", count: 400, on_hand: 3000, shortage: 50 },
    { key: "21", count: 300, on_hand: 2000, shortage: 30 },
    { key: "24", count: 250, on_hand: 1800, shortage: 28 },
    { key: "27", count: 220, on_hand: 1600, shortage: 26 },
    { key: "29", count: 200, on_hand: 1500, shortage: 24 },
    { key: "36", count: 180, on_hand: 1400, shortage: 22 },
    { key: "49", count: 160, on_hand: 1300, shortage: 20 },
    { key: "52", count: 140, on_hand: 1200, shortage: 18 },
    { key: "71", count: 120, on_hand: 1100, shortage: 16 },
    { key: "73", count: 100, on_hand: 1000, shortage: 14 },
  ],
  by_part_class: [
    { key: "ROTABLE", count: 9000, on_hand: 60000, shortage: 70 },
    { key: "CONSUMABLE", count: 12215, on_hand: 40000, shortage: 50 },
  ],
  // Previously always [] in this fixture — the BFF-computed breakdown no
  // component ever rendered before F3's drill panels.
  by_tier: [
    { key: "1", count: 3000, on_hand: 20000, shortage: 30 },
    { key: "2", count: 9000, on_hand: 50000, shortage: 60 },
    { key: "3", count: 9215, on_hand: 30000, shortage: 30 },
  ],
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

/** Renders `<Overview>` with the sample fixture stubbed as the fetch response, waiting for load. */
async function renderOverviewLoaded() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleDashboard) }),
  );
  renderWithClient(<Overview />);
  await waitFor(() => expect(screen.getByText("Parts")).toBeInTheDocument());
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

  describe("drill panels (F3)", () => {
    it("opens the ATA-risk card's drill panel showing MORE rows than the top-8 preview list", async () => {
      const user = userEvent.setup();
      await renderOverviewLoaded();

      // The card's own AtaRiskList preview caps at 8 rows — confirm the
      // fixture's 10th-ranked chapter (key "73", the lowest shortage) is
      // NOT among the preview's rendered rows before opening the panel.
      expect(screen.queryByText("ATA 73")).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Risk by ATA chapter" }));

      const panel = await screen.findByRole("region", { name: /Risk by ATA chapter — full list/i });
      expect(within(panel).getByText(/not just the top 8/i)).toBeInTheDocument();

      // All 10 by_ata rows are present in the panel's table (bare key, not "ATA "-prefixed —
      // BreakdownTable renders the raw Breakdown.key for this spec, which has no labelFor).
      // Scoped per-row (via the label cell) rather than a bare panel-wide text search,
      // since a key like "24" can also appear verbatim in another row's shortage/on-hand cell.
      const dataRows = within(panel).getAllByRole("row").slice(1); // drop the header row
      expect(dataRows).toHaveLength(10);
      for (const key of ["32", "21", "24", "27", "29", "36", "49", "52", "71", "73"]) {
        const row = dataRows.find((candidate) => within(candidate).queryAllByRole("cell")[0]?.textContent === key);
        expect(row, `expected a row labeled "${key}"`).toBeDefined();
      }
    });

    it("opens the by-tier drill panel (a breakdown the BFF computes but no card renders directly)", async () => {
      const user = userEvent.setup();
      await renderOverviewLoaded();

      // by_tier has no dedicated Overview card — reached via the AOG-exposure
      // KPI card per KPI_DRILL_MAP.
      await user.click(screen.getByRole("button", { name: "AOG exposure" }));

      const panel = await screen.findByRole("region", { name: /Breakdown by autonomy tier/i });
      // Tier labels come from the real Workbench TIER_LABEL map (Tier A/B/C), not raw "1"/"2"/"3".
      expect(within(panel).getByText("Tier A · Advisor")).toBeInTheDocument();
      expect(within(panel).getByText("Tier B · Bounded")).toBeInTheDocument();
      expect(within(panel).getByText("Tier C · Autonomous")).toBeInTheDocument();
    });

    it("enforces a single-open invariant — opening one card's panel closes another", async () => {
      const user = userEvent.setup();
      await renderOverviewLoaded();

      await user.click(screen.getByRole("button", { name: "Risk by ATA chapter" }));
      expect(await screen.findByRole("region", { name: /Risk by ATA chapter/i })).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Priority actions" }));
      expect(await screen.findByRole("region", { name: /Priority actions — full list/i })).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: /Risk by ATA chapter/i })).not.toBeInTheDocument();

      // Only one panel region is ever mounted at a time.
      expect(screen.getAllByRole("region")).toHaveLength(1);
    });

    it("restores focus to the opening trigger when the panel's close button is clicked", async () => {
      const user = userEvent.setup();
      await renderOverviewLoaded();

      const trigger = screen.getByRole("button", { name: "Risk by ATA chapter" });
      await user.click(trigger);

      const panel = await screen.findByRole("region", { name: /Risk by ATA chapter/i });
      await user.click(within(panel).getByRole("button", { name: /^Close/ }));

      expect(screen.queryByRole("region")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });

    it("closes the open panel on Escape and restores focus to the trigger", async () => {
      const user = userEvent.setup();
      await renderOverviewLoaded();

      const trigger = screen.getByRole("button", { name: "Priority actions" });
      await user.click(trigger);
      await screen.findByRole("region", { name: /Priority actions — full list/i });

      await user.keyboard("{Escape}");

      expect(screen.queryByRole("region")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });

    it("marks the trigger aria-expanded=true while open and false once closed", async () => {
      const user = userEvent.setup();
      await renderOverviewLoaded();

      const trigger = screen.getByRole("button", { name: "Parts" });
      expect(trigger).toHaveAttribute("aria-expanded", "false");

      await user.click(trigger);
      expect(trigger).toHaveAttribute("aria-expanded", "true");

      await user.click(trigger);
      expect(trigger).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByRole("region")).not.toBeInTheDocument();
    });
  });
});
