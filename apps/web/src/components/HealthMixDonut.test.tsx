import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HealthMixDonut } from "@/components/HealthMixDonut";

describe("HealthMixDonut", () => {
  it("renders an accessible donut with a legend that is not color-only", () => {
    render(
      <HealthMixDonut
        slices={[
          { key: "1", count: 60, on_hand: 100, shortage: 5 },
          { key: "2", count: 40, on_hand: 80, shortage: 10 },
        ]}
        labelFor={(key) => `Tier ${key}`}
      />,
    );

    const donut = screen.getByRole("img", { name: /inventory health mix by count/i });
    expect(donut).toHaveAccessibleName(/Tier 1: 60 \(60%\)/);
    expect(donut).toHaveAccessibleName(/Tier 2: 40 \(40%\)/);

    // Legend renders text labels + values alongside color swatches (WCAG: never color-only).
    expect(screen.getByText("Tier 1")).toBeInTheDocument();
    expect(screen.getByText("60 (60%)")).toBeInTheDocument();
    expect(screen.getByText("Tier 2")).toBeInTheDocument();
    expect(screen.getByText("40 (40%)")).toBeInTheDocument();

    // Center total label.
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("renders an empty state when there is no data", () => {
    render(<HealthMixDonut slices={[]} />);
    expect(screen.getByText("No health-mix data available.")).toBeInTheDocument();
  });
});
