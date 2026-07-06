import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PartDrillDown } from "@/features/part/PartDrillDown";
import { bffClient } from "@/lib/api/client";
import type { HistoryEntry, PartContext } from "@/lib/api/types";

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

function stubFetch(history: HistoryEntry[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("/history")) return Promise.resolve({ ok: true, json: () => Promise.resolve(history) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(samplePartContext) });
    }),
  );
}

function renderWithProviders(ui: ReactElement, initialPath: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/parts/:pn/:location" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PartDrillDown", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state, then renders header, stat metrics, and provenance chips", async () => {
    stubFetch();

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);

    await waitFor(() => expect(screen.getByText("19000-231-3")).toBeInTheDocument());

    // Header
    expect(screen.getByText("YYC")).toBeInTheDocument();
    expect(screen.getByText("WATER TANK HEATER BLANKET")).toBeInTheDocument();
    expect(screen.getByTestId("criticality-badge")).toHaveTextContent("Tier 2");
    expect(screen.getByText("ATA 38")).toBeInTheDocument();

    // Stat cards — every metric goes through Metric+ProvChip
    expect(screen.getByText("Stock position")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument(); // on-hand
    expect(screen.getByText("Policy — current vs proposed")).toBeInTheDocument();
    expect(screen.getByText("Unit cost")).toBeInTheDocument();
    expect(screen.getByText("$245.50")).toBeInTheDocument();
    expect(screen.getByText("Open orders")).toBeInTheDocument();
    expect(screen.getByText("PO-1")).toBeInTheDocument();

    // Provenance invariant: every stat card carries a ProvChip.
    expect(screen.getAllByTestId("prov-chip").length).toBeGreaterThanOrEqual(7);
  });

  it("renders an error state when the BFF call fails (unknown part)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        statusText: "Not Found",
        json: () => Promise.resolve({ detail: "unknown-pn/nowhere" }),
      }),
    );

    renderWithProviders(<PartDrillDown />, "/parts/unknown-pn/nowhere");

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/failed to load part/i);
  });

  it("renders empty states gracefully when demand/open orders are absent", async () => {
    const emptyContext: PartContext = {
      ...samplePartContext,
      demand: null,
      open_orders: [],
      total_open_qty: 0,
      lead_time: null,
      unit_cost: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/history")) return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
        return Promise.resolve({ ok: true, json: () => Promise.resolve(emptyContext) });
      }),
    );

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    await waitFor(() => expect(screen.getByText("19000-231-3")).toBeInTheDocument());

    expect(screen.getByText("No demand history for this part.")).toBeInTheDocument();
    expect(screen.getByText("No open orders.")).toBeInTheDocument();
    expect(screen.getByText("No lead time data.")).toBeInTheDocument();
    expect(screen.getByText("No vendor economics on record.")).toBeInTheDocument();
  });

  it("renders the writeback history section and rolls back via the confirm dialog", async () => {
    stubFetch([
      { tenant_id: "acme", pn: "19000-231-3", location: "YYC", version: 1, status: "written",
        old_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
        new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
        provenance_id: "p", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
        idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z" },
    ]);
    const rollbackSpy = vi.spyOn(bffClient, "rollback").mockResolvedValue({
      tenant_id: "acme", pn: "19000-231-3", location: "YYC", status: "rolled_back",
      from_values: null, to_values: null, reverted_from_version: 1, new_version: 2,
      rolled_back_at: "2026-07-06T00:00:00Z", error_message: null,
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");
    await userEvent.click(await screen.findByRole("button", { name: /roll back/i }));
    await userEvent.type(screen.getByLabelText(/reason/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /confirm rollback/i }));
    await waitFor(() => expect(rollbackSpy).toHaveBeenCalledWith(
      expect.objectContaining({ pn: "19000-231-3", location: "YYC", reason: "wrong", principal: "planner" }),
      expect.anything(),
    ));
  });
});
