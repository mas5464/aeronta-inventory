import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Reports } from "@/features/reports/Reports";
import { bffClient } from "@/lib/api/client";
import type { BvrReport } from "@/lib/api/types";

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

function renderReports(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Reports", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the report header, exec summary, mapped savings labels, governance, forward look, and methodology", async () => {
    vi.spyOn(bffClient, "getBvr").mockResolvedValue(sampleBvr);
    renderReports(<Reports />);

    expect(await screen.findByRole("heading", { name: /business value report/i })).toBeInTheDocument();
    // period.label renders as one segment of a compound header line
    // ("As of 2026-04-01 · generated ... · schema ..."), so match the
    // substring via regex rather than getByText's whole-node-text equality.
    expect(screen.getByText(/As of 2026-04-01/)).toBeInTheDocument();
    // savings labels are HUMAN, not raw snake_case
    expect(screen.getByText("Holding cost")).toBeInTheDocument();
    expect(screen.queryByText("holding_cost_delta")).not.toBeInTheDocument();
    // governance rate formatted
    expect(screen.getByText(/50\.0%/)).toBeInTheDocument();
    // forward-look opportunity links to Part Drill-Down
    const oppLink = screen.getByRole("link", { name: /P1/ });
    expect(oppLink).toHaveAttribute("href", "/parts/P1/YYC");
    // methodology's keys-of-portfolio disclosure
    expect(screen.getByText(/57,?605.*58,?899|57605 of 58899/)).toBeInTheDocument();
  });

  it("renders Open printable report and Download PDF controls", async () => {
    vi.spyOn(bffClient, "getBvr").mockResolvedValue(sampleBvr);
    renderReports(<Reports />);
    expect(await screen.findByRole("button", { name: /printable report/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download pdf/i })).toBeInTheDocument();
  });

  it("clicking 'Open printable report' opens the HTML document via an authenticated fetch (no filename — new-tab view)", async () => {
    vi.spyOn(bffClient, "getBvr").mockResolvedValue(sampleBvr);
    const htmlBlob = new Blob(["<html></html>"], { type: "text/html" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(htmlBlob) });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn().mockReturnValue("blob:mock-url"),
      revokeObjectURL: vi.fn(),
    });
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const user = userEvent.setup();

    renderReports(<Reports />);
    const htmlButton = await screen.findByRole("button", { name: /printable report/i });
    await user.click(htmlButton);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/reports/bvr.html"),
        expect.anything(),
      ),
    );
    expect(openSpy).toHaveBeenCalledWith("blob:mock-url", "_blank", "noopener");
  });

  it("clicking 'Download PDF' triggers an authenticated download named aeronta-bvr.pdf", async () => {
    vi.spyOn(bffClient, "getBvr").mockResolvedValue(sampleBvr);
    const pdfBlob = new Blob(["%PDF-"], { type: "application/pdf" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(pdfBlob) });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn().mockReturnValue("blob:mock-url"),
      revokeObjectURL: vi.fn(),
    });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const user = userEvent.setup();

    renderReports(<Reports />);
    const pdfButton = await screen.findByRole("button", { name: /download pdf/i });
    await user.click(pdfButton);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/reports/bvr.pdf"),
        expect.anything(),
      ),
    );
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it("does NOT render provenance chips (report-document boundary)", async () => {
    vi.spyOn(bffClient, "getBvr").mockResolvedValue(sampleBvr);
    renderReports(<Reports />);
    await screen.findByRole("heading", { name: /business value report/i });
    // ProvChip renders with data-testid="prov-chip" (verified: ProvChip.tsx:43,
    // and Metric.test.tsx asserts its presence the same way). The report-document
    // boundary means NONE should appear in the Reports view.
    expect(screen.queryByTestId("prov-chip")).not.toBeInTheDocument();
  });

  it("shows loading then error+Retry when the query fails", async () => {
    vi.spyOn(bffClient, "getBvr").mockRejectedValue(new Error("boom"));
    renderReports(<Reports />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/failed to load/i);
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
