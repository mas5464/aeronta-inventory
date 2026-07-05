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

  it("positions bars by real elapsed time, not array index", () => {
    const points = [
      { period_start: "2024-01-01", removals: 1, issues: 0, total: 1 },
      { period_start: "2024-01-31", removals: 1, issues: 0, total: 1 },
      { period_start: "2026-01-01", removals: 1, issues: 0, total: 1 },
    ];
    const { container } = render(<DemandTrend points={points} />);
    const rects = container.querySelectorAll("rect");
    expect(rects).toHaveLength(3);
    const [x1, x2, x3] = Array.from(rects).map((r) => Number(r.getAttribute("x")));
    // Bar 1 and bar 2 are 30 days apart out of a ~2-year total span: much
    // closer together than bar 2 and bar 3, which are ~2 years apart.
    expect(x2 - x1).toBeLessThan(x3 - x2);
    expect(x2 - x1).toBeLessThan(20);
  });

  it("caps bar width at a fixed size regardless of point count", () => {
    const points = [
      { period_start: "2026-01-01", removals: 1, issues: 0, total: 1 },
      { period_start: "2026-02-01", removals: 1, issues: 0, total: 1 },
    ];
    const { container } = render(<DemandTrend points={points} />);
    const widths = Array.from(container.querySelectorAll("rect")).map((r) =>
      r.getAttribute("width"),
    );
    expect(widths).toEqual(["10", "10"]);
  });

  it("draws gridlines for a multi-year span", () => {
    const points = [
      { period_start: "2024-01-01", removals: 1, issues: 0, total: 1 },
      { period_start: "2026-01-01", removals: 1, issues: 0, total: 1 },
    ];
    const { container } = render(<DemandTrend points={points} />);
    expect(container.querySelectorAll("line").length).toBeGreaterThan(0);
  });

  it("shows the real observed date range in a caption", () => {
    const points = [
      { period_start: "2024-01-15", removals: 1, issues: 0, total: 1 },
      { period_start: "2026-06-15", removals: 1, issues: 0, total: 1 },
    ];
    render(<DemandTrend points={points} />);
    expect(screen.getByText("Demand history: Jan 2024 – Jun 2026")).toBeInTheDocument();
  });

  it("shows a single date with no range for one point", () => {
    render(
      <DemandTrend points={[{ period_start: "2025-03-01", removals: 1, issues: 0, total: 1 }]} />,
    );
    expect(screen.getByText("Demand history: Mar 2025")).toBeInTheDocument();
    expect(screen.getAllByRole("img")).toHaveLength(1);
  });
});
