import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { FakePlannerClient } from "./api/client";
import { SAMPLE_SEED } from "./api/sample";

// Shallow-copy the seed entries so each test gets an isolated client (the fake
// reassigns .row/.detail rather than mutating in place, so this is enough).
function freshClient() {
  return new FakePlannerClient(SAMPLE_SEED.map((e) => ({ ...e })));
}

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

  it("rejecting from the detail panel removes the row", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    await userEvent.click(await screen.findByText("FILTER-EXP-042"));
    const reject = await screen.findByRole("button", { name: "Reject" });
    await userEvent.click(reject);
    await waitFor(() => expect(screen.getByText("acme · 3 pending")).toBeInTheDocument());
    expect(screen.queryByText("FILTER-EXP-042")).not.toBeInTheDocument();
  });
});
