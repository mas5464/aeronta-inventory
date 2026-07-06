import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RollbackConfirmDialog } from "@/features/part/RollbackConfirmDialog";
import type { HistoryEntry } from "@/lib/api/types";

const entry: HistoryEntry = {
  tenant_id: "acme", pn: "P1", location: "YYC", version: 3, status: "written",
  old_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
  new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
  provenance_id: "prov-1", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
  idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z",
};

describe("RollbackConfirmDialog", () => {
  it("shows the from→to values and requires a reason before confirming", async () => {
    const onConfirm = vi.fn();
    render(<RollbackConfirmDialog entry={entry} onCancel={vi.fn()} onConfirm={onConfirm} />);
    // from = new_values, to = old_values
    expect(screen.getByText(/ROP 3 · EOQ 5 · SS 2 · Max 8/)).toBeInTheDocument();
    expect(screen.getByText(/ROP 2 · EOQ 4 · SS 1 · Max 6/)).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: /confirm rollback/i });
    expect(confirm).toBeDisabled(); // no reason yet
    await userEvent.type(screen.getByLabelText(/reason/i), "policy was wrong");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith("policy was wrong");
  });

  it("calls onCancel from the Cancel button", async () => {
    const onCancel = vi.fn();
    render(<RollbackConfirmDialog entry={entry} onCancel={onCancel} onConfirm={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("surfaces a result error inline", () => {
    render(<RollbackConfirmDialog entry={entry} onCancel={vi.fn()} onConfirm={vi.fn()} resultError="outside rollback window" />);
    expect(screen.getByText(/outside rollback window/i)).toBeInTheDocument();
  });

  it("is a labelled modal dialog", () => {
    render(<RollbackConfirmDialog entry={entry} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    const dlg = screen.getByRole("dialog");
    expect(dlg).toHaveAttribute("aria-modal", "true");
  });
});
