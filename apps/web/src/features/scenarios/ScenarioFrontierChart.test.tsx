import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ScenarioFrontierChart } from "@/features/scenarios/ScenarioFrontierChart";
import type { FrontierPoint, ScenarioOutcome } from "@/lib/api/types";

const frontier: FrontierPoint[] = [
  { service_level: 0.9, projected_investment: 900_000, projected_coverage: 0.9 },
  { service_level: 0.95, projected_investment: 1_000_000, projected_coverage: 0.95 },
  { service_level: 0.99, projected_investment: 1_300_000, projected_coverage: 0.99 },
];

const current: ScenarioOutcome = {
  service_level: 0.95,
  projected_investment: 1_000_000,
  projected_coverage: 0.95,
  on_hand_gap_ratio: 0.8,
  scored_keys: 21215,
};

const proposed: ScenarioOutcome = {
  service_level: 0.97,
  projected_investment: 1_150_000,
  projected_coverage: 0.97,
  on_hand_gap_ratio: 0.78,
  scored_keys: 21215,
};

describe("ScenarioFrontierChart", () => {
  it("renders an accessible SVG with a descriptive label", () => {
    render(<ScenarioFrontierChart frontier={frontier} current={current} proposed={proposed} />);

    const svg = screen.getByRole("img", { name: /cost-service frontier/i });
    expect(svg).toBeInTheDocument();
  });

  it("renders current and proposed markers", () => {
    render(<ScenarioFrontierChart frontier={frontier} current={current} proposed={proposed} />);

    expect(screen.getByTestId("frontier-current-marker")).toBeInTheDocument();
    expect(screen.getByTestId("frontier-proposed-marker")).toBeInTheDocument();
  });

  it("renders one point per frontier entry", () => {
    const { container } = render(
      <ScenarioFrontierChart frontier={frontier} current={current} proposed={proposed} />,
    );
    // 3 frontier dots + 1 current marker + 1 proposed marker = 5 circles.
    expect(container.querySelectorAll("circle")).toHaveLength(5);
  });

  it("renders a legend distinguishing current plan, proposed scenario, and frontier points", () => {
    render(<ScenarioFrontierChart frontier={frontier} current={current} proposed={proposed} />);

    expect(screen.getByText("Current plan")).toBeInTheDocument();
    expect(screen.getByText("Proposed scenario")).toBeInTheDocument();
    expect(screen.getByText("Frontier point")).toBeInTheDocument();
  });

  it("renders an empty state when the frontier is empty", () => {
    render(<ScenarioFrontierChart frontier={[]} current={current} proposed={proposed} />);
    expect(screen.getByText("No frontier data available.")).toBeInTheDocument();
  });
});
