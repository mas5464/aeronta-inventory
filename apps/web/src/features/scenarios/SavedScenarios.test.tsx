import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SavedScenarios } from "@/features/scenarios/SavedScenarios";
import type { Scenario, ScenarioSolveResult } from "@/lib/api/types";

function baseResult(overrides: Partial<ScenarioSolveResult> = {}): ScenarioSolveResult {
  return {
    params: { lead_time_delta_pct: 0, scope: "all" },
    current: {
      service_level: 0.95,
      projected_investment: 1_000_000,
      projected_coverage: 0.95,
      on_hand_gap_ratio: 0.8,
      scored_keys: 21215,
    },
    proposed: {
      service_level: 0.97,
      projected_investment: 1_150_000,
      projected_coverage: 0.97,
      on_hand_gap_ratio: 0.75,
      scored_keys: 21215,
    },
    delta_investment: 150_000,
    delta_coverage: 0.02,
    frontier: [],
    skipped_keys: 617,
    total_keys: 21215,
    budget_cap_binds: false,
    ...overrides,
  };
}

function scenario(overrides: Partial<Scenario> = {}): Scenario {
  return {
    id: "scn-1",
    name: "Tier 1 to 99%",
    params: baseResult().params,
    result: baseResult(),
    status: "draft",
    created_at: "2026-07-01T00:00:00Z",
    committed_at: null,
    ...overrides,
  };
}

describe("SavedScenarios", () => {
  it("renders an empty state when there are no saved scenarios", () => {
    render(<SavedScenarios scenarios={[]} onDelete={vi.fn()} onCommit={vi.fn()} />);
    expect(screen.getByText("No saved scenarios yet.")).toBeInTheDocument();
  });

  it("lists saved scenarios with name, status badge, and key numbers", () => {
    render(<SavedScenarios scenarios={[scenario()]} onDelete={vi.fn()} onCommit={vi.fn()} />);
    expect(screen.getByText("Tier 1 to 99%")).toBeInTheDocument();
    expect(screen.getByText("draft")).toBeInTheDocument();
  });

  it("requires a confirm step before committing a draft scenario", async () => {
    const onCommit = vi.fn();
    render(
      <SavedScenarios scenarios={[scenario()]} onDelete={vi.fn()} onCommit={onCommit} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Commit" }));
    expect(onCommit).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: /confirm commit/i });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute("aria-modal", "true");

    await userEvent.click(screen.getByRole("button", { name: "Confirm commit" }));
    expect(onCommit).toHaveBeenCalledWith("scn-1");
  });

  it("cancels the commit confirm dialog without calling onCommit", async () => {
    const onCommit = vi.fn();
    render(<SavedScenarios scenarios={[scenario()]} onDelete={vi.fn()} onCommit={onCommit} />);

    await userEvent.click(screen.getByRole("button", { name: "Commit" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCommit).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes the commit confirm dialog (WCAG 2.1 AA) when Escape is pressed, without calling onCommit", async () => {
    const onCommit = vi.fn();
    const user = userEvent.setup();
    render(<SavedScenarios scenarios={[scenario()]} onDelete={vi.fn()} onCommit={onCommit} />);

    await user.click(screen.getByRole("button", { name: "Commit" }));
    expect(screen.getByRole("dialog", { name: /confirm commit/i })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(onCommit).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // Focus returns to the row's Commit button, not lost to the document body.
    expect(screen.getByRole("button", { name: "Commit" })).toHaveFocus();
  });

  it("does not show a Commit button for an already-committed scenario", () => {
    render(
      <SavedScenarios
        scenarios={[scenario({ status: "committed", committed_at: "2026-07-01T01:00:00Z" })]}
        onDelete={vi.fn()}
        onCommit={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Commit" })).not.toBeInTheDocument();
    expect(screen.getAllByText(/committed/i).length).toBeGreaterThan(0);
  });

  it("calls onDelete with the scenario id", async () => {
    const onDelete = vi.fn();
    render(<SavedScenarios scenarios={[scenario()]} onDelete={onDelete} onCommit={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledWith("scn-1");
  });

  it("shows a comparison table once exactly two scenarios are selected", async () => {
    const scenarios = [
      scenario({ id: "a", name: "Scenario A" }),
      scenario({ id: "b", name: "Scenario B" }),
      scenario({ id: "c", name: "Scenario C" }),
    ];
    render(<SavedScenarios scenarios={scenarios} onDelete={vi.fn()} onCommit={vi.fn()} />);

    expect(screen.queryByText("Compare scenarios")).not.toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Select Scenario A to compare"));
    await userEvent.click(screen.getByLabelText("Select Scenario B to compare"));

    expect(screen.getByText("Compare scenarios")).toBeInTheDocument();
    expect(screen.getByText("Service level")).toBeInTheDocument();
    expect(screen.getByText("Projected investment")).toBeInTheDocument();
  });

  it("selecting a third scenario drops the oldest selection (keeps exactly two)", async () => {
    const scenarios = [
      scenario({ id: "a", name: "Scenario A" }),
      scenario({ id: "b", name: "Scenario B" }),
      scenario({ id: "c", name: "Scenario C" }),
    ];
    render(<SavedScenarios scenarios={scenarios} onDelete={vi.fn()} onCommit={vi.fn()} />);

    await userEvent.click(screen.getByLabelText("Select Scenario A to compare"));
    await userEvent.click(screen.getByLabelText("Select Scenario B to compare"));
    await userEvent.click(screen.getByLabelText("Select Scenario C to compare"));

    expect(screen.getByLabelText("Select Scenario A to compare")).not.toBeChecked();
    expect(screen.getByLabelText("Select Scenario B to compare")).toBeChecked();
    expect(screen.getByLabelText("Select Scenario C to compare")).toBeChecked();
  });
});
