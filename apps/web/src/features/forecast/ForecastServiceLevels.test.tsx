import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ForecastServiceLevels } from "@/features/forecast/ForecastServiceLevels";
import type { ForecastSummary } from "@/lib/api/types";

const sampleForecast: ForecastSummary = {
  service_levels: {
    bands: [
      { criticality_tier: 1, target_service_level: 0.995, sku_count: 400, actual_coverage: 0.9 },
      { criticality_tier: 2, target_service_level: 0.98, sku_count: 700, actual_coverage: 0.85 },
      { criticality_tier: 3, target_service_level: 0.95, sku_count: 0, actual_coverage: null },
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
    note: "No backtest runs at serve time — real DEMAND_HISTORY actuals vs. projection.",
    points: [
      { period_start: "2026-05-01", actual: 40, projected: 35 },
      { period_start: "2026-06-01", actual: 44, projected: 38 },
    ],
  },
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

describe("ForecastServiceLevels", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state, then the forecast KPIs with provenance", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleForecast) }),
    );

    renderWithClient(<ForecastServiceLevels />);

    expect(screen.getByRole("status", { name: "" })).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText("1,100")).toBeInTheDocument());
    expect(screen.getByText("SKUs on ML/statistical forecast")).toBeInTheDocument();
    expect(screen.getByText("SL policy tiers configured")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();

    expect(screen.getAllByTestId("prov-chip").length).toBeGreaterThanOrEqual(2);
  });

  it("renders the service-level policy table with real targets and honest coverage proxy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleForecast) }),
    );

    renderWithClient(<ForecastServiceLevels />);

    await waitFor(() =>
      expect(screen.getByText("Service-level policy by criticality")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Tier 1 — No-Go \(MEL\)/)).toBeInTheDocument();
    expect(screen.getByText("99.5%")).toBeInTheDocument();
    // tier 3 has no keys -> actual_coverage is null -> renders em dash
    expect(screen.getByText(/Tier 3/)).toBeInTheDocument();
  });

  it("renders forecast-method coverage bars from the real regime classification", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleForecast) }),
    );

    renderWithClient(<ForecastServiceLevels />);

    await waitFor(() => expect(screen.getByText("Forecast-method coverage")).toBeInTheDocument());
    expect(screen.getByText("Croston/SBA/TSB")).toBeInTheDocument();
    expect(screen.getByText("Historical + scheduled (moving average)")).toBeInTheDocument();
  });

  it("renders the accuracy band with an honest not-yet-connected disclosure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleForecast) }),
    );

    renderWithClient(<ForecastServiceLevels />);

    await waitFor(() =>
      expect(screen.getByText("Network actual vs. forecast")).toBeInTheDocument(),
    );
    expect(screen.getByText(/not yet connected/i)).toBeInTheDocument();
    expect(screen.getByText(/No backtest runs at serve time/)).toBeInTheDocument();
    expect(screen.getByText("2026-05-01")).toBeInTheDocument();
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

    renderWithClient(<ForecastServiceLevels />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/failed to load forecast/i);
  });
});
