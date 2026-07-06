import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WritebackHistory } from "@/features/part/WritebackHistory";
import { bffClient } from "@/lib/api/client";
import type { HistoryEntry } from "@/lib/api/types";

function entry(over: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    tenant_id: "acme", pn: "P1", location: "YYC", version: 1, status: "written",
    old_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
    new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
    provenance_id: "prov-1", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
    idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z", ...over,
  };
}

function renderIt(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("WritebackHistory", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a newest-first timeline with value summary + principal", async () => {
    vi.spyOn(bffClient, "getHistory").mockResolvedValue([
      entry({ version: 1 }),
      entry({ version: 2, new_values: { rop: 4, eoq: 6, safety_stock: 3, max_stock: 10 } }),
    ]);
    renderIt(<WritebackHistory pn="P1" location="YYC" onRollback={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/ROP 4/)).toBeInTheDocument());
    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("v2"); // newest first
  });

  it("shows the empty state when there is no history", async () => {
    vi.spyOn(bffClient, "getHistory").mockResolvedValue([]);
    renderIt(<WritebackHistory pn="P1" location="YYC" onRollback={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/No prior writes for P1 · YYC/)).toBeInTheDocument());
  });

  it("disables the rollback button when nothing is revertible", async () => {
    vi.spyOn(bffClient, "getHistory").mockResolvedValue([entry({ status: "shadowed" })]);
    renderIt(<WritebackHistory pn="P1" location="YYC" onRollback={vi.fn()} />);
    const btn = await screen.findByRole("button", { name: /roll back/i });
    expect(btn).toBeDisabled();
  });

  it("enables rollback and calls onRollback with the revertible entry", async () => {
    const onRollback = vi.fn();
    vi.spyOn(bffClient, "getHistory").mockResolvedValue([entry({ version: 1 })]);
    renderIt(<WritebackHistory pn="P1" location="YYC" onRollback={onRollback} />);
    // Wait for the history query to settle — the button renders (disabled)
    // during the pending state too, so findByRole alone can resolve before
    // the revertible entry has loaded.
    await waitFor(() => expect(screen.getByText(/ROP 3/)).toBeInTheDocument());
    const btn = screen.getByRole("button", { name: /roll back/i });
    expect(btn).toBeEnabled();
    btn.click();
    expect(onRollback).toHaveBeenCalledWith(expect.objectContaining({ version: 1 }));
  });
});
