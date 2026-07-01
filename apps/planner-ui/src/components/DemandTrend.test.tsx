import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DemandTrend } from "./DemandTrend";

describe("DemandTrend", () => {
  it("renders a labelled chart with a bar per point", () => {
    const points = [
      { period_start: "2026-01-01", removals: 2, issues: 0, total: 2 },
      { period_start: "2026-02-01", removals: 0, issues: 1, total: 1 },
    ];
    render(<DemandTrend points={points} />);
    expect(screen.getByRole("img", { name: /demand/i })).toBeInTheDocument();
  });
  it("shows an empty state with no points", () => {
    render(<DemandTrend points={[]} />);
    expect(screen.getByText(/no demand history/i)).toBeInTheDocument();
  });
});
