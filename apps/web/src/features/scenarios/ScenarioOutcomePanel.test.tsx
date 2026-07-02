import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ScenarioOutcomePanel } from "@/features/scenarios/ScenarioOutcomePanel";
import type { ScenarioSolveResult } from "@/lib/api/types";

function result(overrides: Partial<ScenarioSolveResult> = {}): ScenarioSolveResult {
  return {
    params: { lead_time_delta_pct: 0, scope: "all", service_level_target: 0.95 },
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

describe("ScenarioOutcomePanel", () => {
  it("renders projected investment, deltas, and provenance chips", () => {
    render(<ScenarioOutcomePanel result={result()} />);

    expect(screen.getByText("$1,150,000")).toBeInTheDocument();
    expect(screen.getByText("Projected investment")).toBeInTheDocument();
    expect(screen.getByText("Investment delta vs. plan")).toBeInTheDocument();
    expect(screen.getByText("Coverage delta vs. plan")).toBeInTheDocument();
    expect(screen.getAllByTestId("prov-chip").length).toBeGreaterThan(0);
  });

  it("shows the on-hand-gap metric distinct from coverage", () => {
    render(<ScenarioOutcomePanel result={result()} />);
    expect(screen.getByText("Parts already at target (on-hand)")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
  });

  it("shows the honest skipped-keys disclosure when skipped_keys > 0", () => {
    render(<ScenarioOutcomePanel result={result({ skipped_keys: 617, total_keys: 21215 })} />);
    expect(screen.getByText(/617 of 21,215 parts network-wide/)).toBeInTheDocument();
  });

  it("does not show the skipped-keys disclosure when skipped_keys is 0", () => {
    render(<ScenarioOutcomePanel result={result({ skipped_keys: 0 })} />);
    expect(screen.queryByText(/parts network-wide/)).not.toBeInTheDocument();
  });

  it("shows a budget-cap-exceeded alert when budget_cap_binds is true", () => {
    render(<ScenarioOutcomePanel result={result({ budget_cap_binds: true })} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/exceeds the budget cap/i);
  });

  it("does not show a budget alert when budget_cap_binds is false", () => {
    render(<ScenarioOutcomePanel result={result({ budget_cap_binds: false })} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
