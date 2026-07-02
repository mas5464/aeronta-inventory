import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { PriorityActionsPreview } from "@/components/PriorityActionsPreview";

describe("PriorityActionsPreview", () => {
  it("renders rows linking to the Part Drill-Down and a view-all link to Workbench", () => {
    render(
      <MemoryRouter>
        <PriorityActionsPreview
          shortages={[
            { pn: "PN-100", location: "JFK", shortage: 20, on_hand: 5, projected_demand: 25 },
          ]}
        />
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: "PN-100" });
    expect(link).toHaveAttribute("href", "/parts/PN-100/JFK");
    expect(screen.getByText("JFK")).toBeInTheDocument();

    const viewAll = screen.getByRole("link", { name: /view all in workbench/i });
    expect(viewAll).toHaveAttribute("href", "/workbench");
  });

  it("renders an empty state when there are no shortages", () => {
    render(
      <MemoryRouter>
        <PriorityActionsPreview shortages={[]} />
      </MemoryRouter>,
    );
    expect(
      screen.getByText("No shortages — nothing to prioritize right now."),
    ).toBeInTheDocument();
  });
});
