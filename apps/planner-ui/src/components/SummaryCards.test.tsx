import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SummaryCards } from "./SummaryCards";

describe("SummaryCards", () => {
  it("renders the four queue KPIs", () => {
    render(<SummaryCards summary={{ count: 4, netCost: 12980, aogRisk: 2, tierA: 3 }} />);
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("$12,980")).toBeInTheDocument();
    expect(screen.getByText("AOG risk")).toBeInTheDocument();
    expect(screen.getByText("Tier A to approve")).toBeInTheDocument();
    // aog + tierA values
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });
});
