import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { FakePlannerClient } from "./api/client";
import { SAMPLE_SEED } from "./api/sample";
import type { HistoryEntry } from "./api/types";

// Shallow-copy the seed entries so each test gets an isolated client (the fake
// reassigns .row/.detail rather than mutating in place, so this is enough).
function freshClient() {
  return new FakePlannerClient(SAMPLE_SEED.map((e) => ({ ...e })));
}

// A prior applied write for HYD-PUMP-001 @ YYZ (the top-priority row) so its history
// timeline is populated and the latest entry is revertible.
const PRIOR_WRITE: HistoryEntry = {
  tenant_id: "acme",
  pn: "HYD-PUMP-001",
  location: "YYZ",
  version: 1,
  status: "written",
  old_values: { rop: 5, eoq: 8, safety_stock: 1, max_stock: 16 },
  new_values: { rop: 6, eoq: 10, safety_stock: 2, max_stock: 20 },
  provenance_id: "prov-prior",
  tier: 1,
  agent_version: "fake-1",
  changed_by_principal: "agent-spine",
  idempotency_key: null,
  parent_version: null,
  changed_at: "2026-06-20T12:00:00Z",
};

function bodyRows() {
  return screen.getAllByRole("row").slice(1);
}

describe("App", () => {
  it("loads the priority-sorted queue", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    expect(await screen.findByText("acme · 4 pending")).toBeInTheDocument();
    expect(bodyRows()).toHaveLength(4);
  });

  it("selecting a row reveals its provenance", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    const matches = await screen.findAllByText("HYD-PUMP-001"); // YYZ and YOW rows
    await userEvent.click(matches[0]);
    expect(await screen.findByText("Why this is queued")).toBeInTheDocument();
    expect(screen.getByText(/requires planner approval/i)).toBeInTheDocument();
  });

  it("approving a policy-bearing row removes it from the queue", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    await screen.findByText("acme · 4 pending");
    const firstRow = bodyRows()[0];
    await userEvent.click(within(firstRow).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByText("acme · 3 pending")).toBeInTheDocument());
    expect(bodyRows()).toHaveLength(3);
  });

  it("engaging the kill switch shows the banner and disables approve", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    await userEvent.click(await screen.findByRole("button", { name: /pause agent/i }));
    expect(await screen.findByText("Agent paused")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/approvals are disabled/i);
    for (const btn of screen.getAllByRole("button", { name: "Approve" })) {
      expect(btn).toBeDisabled();
    }
  });

  it("disables approve buttons while a write is in flight", async () => {
    const fake = freshClient();
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const realApprove = fake.approve.bind(fake);
    // Hold the approve open so we can observe the in-flight state.
    fake.approve = async (tenant, id) => {
      await gate;
      return realApprove(tenant, id);
    };

    render(<App client={fake} tenant="acme" />);
    await screen.findByText("acme · 4 pending");
    const approveButtons = screen.getAllByRole("button", { name: "Approve" });
    await userEvent.click(approveButtons[0]);

    // Every approve button is disabled until the write settles.
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Approve" }).every((b) => b.hasAttribute("disabled"))).toBe(true),
    );

    release();
    await waitFor(() => expect(screen.getByText("acme · 3 pending")).toBeInTheDocument());
  });

  it("bulk-approving Tier A clears the matching approvable rows", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    await screen.findByText("acme · 4 pending");
    await userEvent.click(screen.getByLabelText("Tier A"));
    await userEvent.click(screen.getByRole("button", { name: /approve matching/i }));
    // The 2 Tier-A approvable rows are written; the 2 advisory rows remain.
    await waitFor(() => expect(screen.getByText("acme · 2 pending")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/approved 2 recommendations/i);
  });

  it("surfaces a selected row's writeback history and rolls it back", async () => {
    const client = new FakePlannerClient(SAMPLE_SEED.map((e) => ({ ...e })), [PRIOR_WRITE]);
    render(<App client={client} tenant="acme" />);
    const matches = await screen.findAllByText("HYD-PUMP-001");
    await userEvent.click(matches[0]); // top-priority row = HYD-PUMP-001 @ YYZ
    expect(await screen.findByText(/writeback history/i)).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /roll back/i }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/rolled back/i));
    // The revert appends a v2 entry to the timeline.
    expect(await screen.findByText("v2")).toBeInTheDocument();
  });

  it("an approved row surfaces under the Decided tab with its writeback history", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    await screen.findByText("acme · 4 pending");
    await userEvent.click(within(bodyRows()[0]).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByText("acme · 3 pending")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("tab", { name: /decided/i }));
    await waitFor(() => expect(screen.getByText("acme · 1 decided")).toBeInTheDocument());
    expect(screen.getByText("approved")).toBeInTheDocument(); // status badge, no Approve button
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /HYD-PUMP-001 · YYZ/ }));
    expect(await screen.findByText(/writeback history/i)).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument(); // the write recorded on approve
  });

  it("rejecting from the detail panel removes the row", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    await userEvent.click(await screen.findByText("FILTER-EXP-042"));
    const reject = await screen.findByRole("button", { name: "Reject" });
    await userEvent.click(reject);
    await waitFor(() => expect(screen.getByText("acme · 3 pending")).toBeInTheDocument());
    expect(screen.queryByText("FILTER-EXP-042")).not.toBeInTheDocument();
  });
});
