import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SAMPLE_SEED } from "../api/sample";
import type { HistoryEntry } from "../api/types";
import { DetailPanel } from "./DetailPanel";

const POLICY_DETAIL = SAMPLE_SEED[0].detail; // HYD-PUMP-001 @ YYZ, has a proposed policy
const ADVISORY_DETAIL = SAMPLE_SEED[2].detail; // FILTER-EXP-042, advisory (no proposed policy)

function writeEntry(over: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    tenant_id: "acme",
    pn: "HYD-PUMP-001",
    location: "YYZ",
    version: 1,
    status: "written",
    old_values: { rop: 6, eoq: 10, safety_stock: 2, max_stock: 20 },
    new_values: { rop: 9, eoq: 12, safety_stock: 4, max_stock: 24 },
    provenance_id: "prov-7af3",
    tier: 1,
    agent_version: "fake-1",
    changed_by_principal: "agent-spine",
    idempotency_key: null,
    parent_version: null,
    changed_at: "2026-06-20T12:00:00Z",
    ...over,
  };
}

describe("DetailPanel", () => {
  it("shows an empty state when nothing is selected", () => {
    render(
      <DetailPanel detail={null} onApprove={vi.fn()} onReject={vi.fn()} onDefer={vi.fn()} />,
    );
    expect(screen.getByText(/select a recommendation/i)).toBeInTheDocument();
  });

  it("renders the current→proposed diff, reason, and evidence", () => {
    render(
      <DetailPanel detail={POLICY_DETAIL} onApprove={vi.fn()} onReject={vi.fn()} onDefer={vi.fn()} />,
    );
    expect(screen.getByText("ROP")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument(); // proposed rop
    expect(screen.getByText(/requires planner approval/i)).toBeInTheDocument();
    expect(screen.getByText(/3 due 2026-05-04/)).toBeInTheDocument();
  });

  it("approve and defer fire their handlers", async () => {
    const onApprove = vi.fn();
    const onDefer = vi.fn();
    render(
      <DetailPanel
        detail={POLICY_DETAIL}
        onApprove={onApprove}
        onReject={vi.fn()}
        onDefer={onDefer}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith("rec-hyd-yyz");
    await userEvent.click(screen.getByRole("button", { name: "Defer" }));
    expect(onDefer).toHaveBeenCalledWith("rec-hyd-yyz");
  });

  it("reject fires onReject with the selected reason", async () => {
    const onReject = vi.fn();
    render(
      <DetailPanel detail={POLICY_DETAIL} onApprove={vi.fn()} onReject={onReject} onDefer={vi.fn()} />,
    );
    await userEvent.selectOptions(screen.getByLabelText("Rejection reason"), "bad_lead_time");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onReject).toHaveBeenCalledWith("rec-hyd-yyz", "bad_lead_time");
  });

  it("disables approve for an advisory (no-policy) recommendation", () => {
    render(
      <DetailPanel detail={ADVISORY_DETAIL} onApprove={vi.fn()} onReject={vi.fn()} onDefer={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByText("Advisory — no writable policy change.")).toBeInTheDocument();
  });

  it("renders the writeback history and fires onRollback for the part/location", async () => {
    const onRollback = vi.fn();
    render(
      <DetailPanel
        detail={POLICY_DETAIL}
        history={[writeEntry()]}
        onRollback={onRollback}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onDefer={vi.fn()}
      />,
    );
    expect(screen.getByText(/writeback history/i)).toBeInTheDocument();
    expect(screen.getByText(/v1/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /roll back/i }));
    expect(onRollback).toHaveBeenCalledWith("HYD-PUMP-001", "YYZ");
  });

  it("shows an empty-history note when there are no prior writes", () => {
    render(
      <DetailPanel
        detail={POLICY_DETAIL}
        history={[]}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onDefer={vi.fn()}
      />,
    );
    expect(screen.getByText(/no prior writes/i)).toBeInTheDocument();
  });

  it("decided mode hides the approve/reject/defer actions but keeps history + rollback", () => {
    render(
      <DetailPanel
        detail={POLICY_DETAIL}
        history={[writeEntry()]}
        decided
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onDefer={vi.fn()}
        onRollback={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Defer" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.getByText(/writeback history/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /roll back/i })).toBeInTheDocument();
  });

  it("disables rollback when the latest write has no known prior value", () => {
    render(
      <DetailPanel
        detail={POLICY_DETAIL}
        history={[writeEntry({ old_values: null })]}
        onRollback={vi.fn()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onDefer={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /roll back/i })).toBeDisabled();
  });
});
