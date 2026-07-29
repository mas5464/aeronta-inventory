import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ScenarioOutcomePanel } from "@/features/scenarios/ScenarioOutcomePanel";
import type { ScenarioSolveResult } from "@/lib/api/types";

function result(overrides: Partial<ScenarioSolveResult> = {}): ScenarioSolveResult {
  return {
    params: {
      lead_time_delta_pct: 0,
      procurement_lead_time_delta_pct: 0,
      repair_tat_delta_pct: 0.25,
      scope: "all",
      service_level_target: 0.95,
    },
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
    contract_version: "scenario-solve.v2",
    repair_current: {
      horizon_days: 90,
      eligible_quantity: 17,
      expected_units: 8.248,
      modeled_keys: 5,
      unavailable_keys: 2,
      unscoped_keys: 1,
      serviceable_yield_assumption: 1,
    },
    repair_proposed: {
      horizon_days: 90,
      eligible_quantity: 17,
      expected_units: 6.742,
      modeled_keys: 5,
      unavailable_keys: 2,
      unscoped_keys: 1,
      serviceable_yield_assumption: 1,
    },
    assumption_impacts: [
      { label: "Repair TAT", affected_key_count: 5 },
    ],
    affected_key_count: 5,
    fingerprint: `scenario_v2_${"a".repeat(64)}`,
    source_as_of: "2026-04-01",
    source_coverage: 0.9,
    source_confidence: 0.9,
    warning_codes: [
      "scenario_repair_serviceable_yield_unobserved",
      "scenario_uniform_rq_approximation",
    ],
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
    expect(screen.getAllByTestId("prov-chip")[0]).toHaveAttribute(
      "aria-label",
      expect.stringContaining("90% coverage"),
    );
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

  it("shows independently modeled repair returns, assumptions, and identity", () => {
    const value = result();
    render(<ScenarioOutcomePanel result={value} />);

    const repair = screen.getByRole("region", {
      name: "Repair return scenario outcome",
    });
    expect(repair).toHaveTextContent("Repair returns within 90 days");
    expect(repair).toHaveTextContent("Current expected8.2");
    expect(repair).toHaveTextContent("Proposed expected6.7");
    expect(repair).toHaveTextContent("Eligible open WIP17");
    expect(repair).toHaveTextContent("Modeled keys5");
    expect(repair).toHaveTextContent(
      "serviceable-yield model assumption, not an observed portfolio yield",
    );
    expect(repair).toHaveTextContent(
      "Procurement lead changes do not alter this repair estimate",
    );
    expect(repair).toHaveTextContent(
      "1 eligible repair keys lacked the selected scope metadata",
    );

    const impacts = screen.getByLabelText("Changed scenario assumptions");
    expect(impacts).toHaveTextContent("Repair TAT · 5 affected keys");
    expect(screen.getByLabelText("Scenario result identity")).toHaveTextContent(
      "scenario-solve.v2",
    );
    expect(screen.getByTestId("scenario-fingerprint")).toHaveTextContent(
      value.fingerprint ?? "",
    );
    expect(screen.getByLabelText("Scenario warnings")).toHaveTextContent(
      "scenario_uniform_rq_approximation",
    );
  });

  it("keeps a legacy result usable without fabricated repair or fingerprint metadata", () => {
    render(
      <ScenarioOutcomePanel
        result={result({
          contract_version: "scenario-solve.v1",
          repair_current: null,
          repair_proposed: null,
          assumption_impacts: [],
          affected_key_count: null,
          fingerprint: null,
          source_as_of: null,
          source_coverage: null,
          source_confidence: null,
          warning_codes: [],
        })}
      />,
    );

    expect(
      screen.queryByRole("region", {
        name: "Repair return scenario outcome",
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Scenario result identity"),
    ).not.toBeInTheDocument();
    expect(screen.getAllByTestId("prov-chip")[0]).toHaveAttribute(
      "aria-label",
      expect.stringContaining("updated unknown, 0% coverage"),
    );
  });
});
