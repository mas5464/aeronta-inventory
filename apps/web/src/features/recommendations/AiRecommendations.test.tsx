import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiRecommendations } from "@/features/recommendations/AiRecommendations";
import type { PagedQueue, QueueRow, RecommendationDetail } from "@/lib/api/types";

function row(overrides: Partial<QueueRow> = {}): QueueRow {
  return {
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
    ...overrides,
  };
}

function detail(overrides: Partial<RecommendationDetail> = {}): RecommendationDetail {
  return {
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
    ...overrides,
  };
}

function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockFetchRouter(opts: {
  queue: PagedQueue;
  details: Record<string, RecommendationDetail>;
  killswitch?: { engaged: boolean };
  onApprove?: () => void;
  onReject?: () => void;
}) {
  const killswitch = opts.killswitch ?? { engaged: false };
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/approve")) {
      opts.onApprove?.();
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ recommendation_id: "rec-1", status: "approved", writeback: null, message: "" }),
      });
    }
    if (url.includes("/reject")) {
      opts.onReject?.();
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ recommendation_id: "rec-1", status: "rejected", writeback: null, message: "" }),
      });
    }
    if (url.includes("/killswitch")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(killswitch) });
    }
    const match = url.match(/\/recommendations\/([^/?]+)$/);
    if (match) {
      const rec = opts.details[match[1]];
      if (rec) return Promise.resolve({ ok: true, json: () => Promise.resolve(rec) });
    }
    if (url.includes("/recommendations")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(opts.queue) });
    }
    return Promise.reject(new Error(`Unhandled fetch: ${url}`));
  });
}

describe("AiRecommendations", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders explainable cards (rec -> reason -> action), cycle summary, and driver panel", async () => {
    const queue: PagedQueue = { items: [row()], total: 1, limit: 50, offset: 0 };
    vi.stubGlobal("fetch", mockFetchRouter({ queue, details: { "rec-1": detail() } }));

    renderWithProviders(<AiRecommendations />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);

    await waitFor(() => expect(screen.getByTestId("recommendation-card")).toBeInTheDocument());

    expect(screen.getByText("Cycle summary")).toBeInTheDocument();
    expect(screen.getByText("How the optimizer decides")).toBeInTheDocument();
    expect(screen.getByText(/Projected shortage within lead time/)).toBeInTheDocument();
    expect(screen.getAllByText("demand_history").length).toBeGreaterThan(0);
  });

  it("fires Accept and Dismiss actions from a recommendation card", async () => {
    const queue: PagedQueue = { items: [row()], total: 1, limit: 50, offset: 0 };
    const onApprove = vi.fn();
    const onReject = vi.fn();
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({ queue, details: { "rec-1": detail() }, onApprove, onReject }),
    );
    const user = userEvent.setup();

    renderWithProviders(<AiRecommendations />);
    await waitFor(() => expect(screen.getByTestId("recommendation-card")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(onApprove).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(onReject).toHaveBeenCalled());
  });

  it("disables Accept when the kill switch is engaged", async () => {
    const queue: PagedQueue = { items: [row()], total: 1, limit: 50, offset: 0 };
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({ queue, details: { "rec-1": detail() }, killswitch: { engaged: true } }),
    );

    renderWithProviders(<AiRecommendations />);
    await waitFor(() => expect(screen.getByTestId("recommendation-card")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
  });

  it("renders an Export CSV link fixed to status=pending", async () => {
    const queue: PagedQueue = { items: [row()], total: 1, limit: 50, offset: 0 };
    vi.stubGlobal("fetch", mockFetchRouter({ queue, details: { "rec-1": detail() } }));

    renderWithProviders(<AiRecommendations />);

    const link = await screen.findByRole("link", { name: /export csv/i });
    const href = link.getAttribute("href") ?? "";
    expect(href).toContain("/recommendations/export.csv?");
    expect(href).toContain("status=pending");
    expect(href).not.toContain("tier=");
    expect(href).not.toContain("type=");
    expect(href).not.toContain("aog_min=");
  });
});
