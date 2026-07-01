import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Workbench } from "@/features/workbench/Workbench";
import { DEFAULT_BFF_URL } from "@/lib/api/client";
import type { PagedQueue, QueueRow } from "@/lib/api/types";

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

function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockFetchRouter(handlers: {
  queue?: PagedQueue;
  killswitch?: { engaged: boolean };
  onApprove?: () => void;
  onReject?: () => void;
  onDefer?: () => void;
  onBulkApprove?: () => void;
}) {
  const queue = handlers.queue ?? { items: [row()], total: 1, limit: 25, offset: 0 };
  const killswitch = handlers.killswitch ?? { engaged: false };

  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.includes("/recommendations/bulk-approve")) {
      handlers.onBulkApprove?.();
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ approved_count: 1, results: [] }),
      });
    }
    if (url.includes("/approve")) {
      handlers.onApprove?.();
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ recommendation_id: "rec-1", status: "approved", writeback: null, message: "" }),
      });
    }
    if (url.includes("/reject")) {
      handlers.onReject?.();
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ recommendation_id: "rec-1", status: "rejected", writeback: null, message: "" }),
      });
    }
    if (url.includes("/defer")) {
      handlers.onDefer?.();
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ recommendation_id: "rec-1", status: "deferred", writeback: null, message: "" }),
      });
    }
    if (url.includes("/killswitch") && method === "POST") {
      killswitch.engaged = !killswitch.engaged;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(killswitch) });
    }
    if (url.includes("/killswitch")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(killswitch) });
    }
    if (url.includes("/recommendations")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(queue) });
    }
    return Promise.reject(new Error(`Unhandled fetch: ${url}`));
  });
}

describe("Workbench", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the ranked worklist with confidence bars and a pager", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({ queue: { items: [row()], total: 60, limit: 25, offset: 0 } }),
    );

    renderWithProviders(<Workbench />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);

    await waitFor(() => expect(screen.getByText("19000-231-3")).toBeInTheDocument());

    expect(screen.getByTestId("confidence-bar")).toHaveTextContent("92%");
    expect(screen.getByText("1–25 of 60")).toBeInTheDocument();
    expect(screen.getByText("Next")).not.toBeDisabled();
    expect(screen.getByText("Previous")).toBeDisabled();
  });

  it("fires Accept/Defer/Dismiss row actions", async () => {
    const onApprove = vi.fn();
    const onDefer = vi.fn();
    const onReject = vi.fn();
    vi.stubGlobal("fetch", mockFetchRouter({ onApprove, onDefer, onReject }));
    const user = userEvent.setup();

    renderWithProviders(<Workbench />);
    await waitFor(() => expect(screen.getByText("19000-231-3")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(onApprove).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "Defer" }));
    await waitFor(() => expect(onDefer).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm dismiss" }));
    await waitFor(() => expect(onReject).toHaveBeenCalled());
  });

  it("disables Accept when the kill switch is engaged and shows a paused banner", async () => {
    vi.stubGlobal("fetch", mockFetchRouter({ killswitch: { engaged: true } }));

    renderWithProviders(<Workbench />);
    await waitFor(() => expect(screen.getByText("19000-231-3")).toBeInTheDocument());

    expect(screen.getByRole("alert")).toHaveTextContent(/approvals are paused/i);
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
  });

  it("applies pill filters client-side over the loaded page", async () => {
    const rows = [
      row({ recommendation_id: "rec-1", pn: "PN-1", tier: 1, type: "purchase" }),
      row({ recommendation_id: "rec-2", pn: "PN-2", tier: 3, type: "transfer" }),
    ];
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({ queue: { items: rows, total: 2, limit: 25, offset: 0 } }),
    );
    const user = userEvent.setup();

    renderWithProviders(<Workbench />);
    await waitFor(() => expect(screen.getByText("PN-1")).toBeInTheDocument());
    expect(screen.getByText("PN-2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Tier A · Advisor" }));

    expect(screen.getByText("PN-1")).toBeInTheDocument();
    expect(screen.queryByText("PN-2")).not.toBeInTheDocument();
  });

  it("bulk-approves the high-confidence candidates on the loaded page", async () => {
    const rows = [
      row({ recommendation_id: "rec-1", pn: "PN-1", confidence_score: 0.95 }),
      row({ recommendation_id: "rec-2", pn: "PN-2", confidence_score: 0.4 }),
    ];
    const onBulkApprove = vi.fn();
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({ queue: { items: rows, total: 2, limit: 25, offset: 0 }, onBulkApprove }),
    );
    const user = userEvent.setup();

    renderWithProviders(<Workbench />);
    await waitFor(() => expect(screen.getByText("PN-1")).toBeInTheDocument());

    const bulkButton = screen.getByRole("button", { name: /Accept high-confidence \(1\)/ });
    await user.click(bulkButton);

    await waitFor(() => expect(onBulkApprove).toHaveBeenCalled());
  });

  it("advances the pager on Next", async () => {
    const fetchMock = mockFetchRouter({
      queue: { items: [row()], total: 60, limit: 25, offset: 0 },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<Workbench />);
    await waitFor(() => expect(screen.getByText("1–25 of 60")).toBeInTheDocument());

    await user.click(screen.getByText("Next"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations?status=pending&limit=25&offset=25`,
        expect.anything(),
      );
    });
  });

  it("disables the Adjust control as coming-soon", async () => {
    vi.stubGlobal("fetch", mockFetchRouter({}));

    renderWithProviders(<Workbench />);
    await waitFor(() => expect(screen.getByText("19000-231-3")).toBeInTheDocument());

    const adjustButtons = screen.getAllByRole("button", { name: /Adjust \(coming soon\)/ });
    for (const button of adjustButtons) {
      expect(button).toBeDisabled();
    }
  });

  /**
   * Large-table strategy (Slice S8 hardening): the Workbench does NOT
   * virtualize — the 40k-SKU strategy is server-side pagination
   * (`PAGE_SIZE <= MAX_PAGE_SIZE`, see queueView.ts). This proves a full
   * `MAX_PAGE_SIZE` (200-row) page — the worst case a single page can ever
   * be — renders completely and promptly with a plain `<table>`, no
   * virtualization library.
   */
  it("renders a full MAX_PAGE_SIZE (200-row) page smoothly, with no virtualization", async () => {
    const rows = Array.from({ length: 200 }, (_, i) =>
      row({ recommendation_id: `rec-${i}`, pn: `PN-${i}` }),
    );
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({ queue: { items: rows, total: 200, limit: 200, offset: 0 } }),
    );

    const start = performance.now();
    renderWithProviders(<Workbench />);
    await waitFor(() => expect(screen.getByText("PN-199")).toBeInTheDocument());
    const elapsedMs = performance.now() - start;

    // Every row actually mounted (no virtualization windowing rows out).
    expect(screen.getAllByRole("row")).toHaveLength(200 + 1); // +1 header row
    expect(screen.getByText("PN-0")).toBeInTheDocument();
    expect(screen.getByText("PN-199")).toBeInTheDocument();
    // Generous budget for a CI-shared runner — the point is "renders at all
    // promptly", not a strict perf benchmark.
    expect(elapsedMs).toBeLessThan(5000);
  });
});
