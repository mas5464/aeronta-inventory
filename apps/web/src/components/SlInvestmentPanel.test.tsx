import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SlInvestmentPanel } from "@/components/SlInvestmentPanel";

describe("SlInvestmentPanel", () => {
  it("honestly discloses that service-level data is not yet connected", () => {
    render(
      <SlInvestmentPanel
        byCriticality={[{ key: "1", count: 10, on_hand: 90, shortage: 10 }]}
        labelFor={(key) => `Tier ${key}`}
      />,
    );

    expect(screen.getByText(/not yet connected/i)).toBeInTheDocument();
    expect(screen.getByText(/does not expose a service-level/i)).toBeInTheDocument();
    expect(screen.getByText("Tier 1")).toBeInTheDocument();
    // on_hand=90, shortage=10 -> 90% coverage.
    expect(screen.getByText("90%")).toBeInTheDocument();
  });

  it("renders an empty state when there is no criticality breakdown", () => {
    render(<SlInvestmentPanel byCriticality={[]} />);
    expect(screen.getByText("No criticality breakdown available.")).toBeInTheDocument();
  });
});
