import type { ReactElement } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { TopShortagesTable } from "@/features/overview/TopShortagesTable";
import type { PartShortfall } from "@/lib/api/types";
import type { Provenance } from "@/lib/provenance";

const provenance: Provenance = {
  source: "eMRO Nightly Extract",
  systemOfRecord: "INVENTORY",
  freshnessAt: new Date().toISOString(),
  coverage: 1,
  confidence: 0.95,
  derived: true,
};

const rows: PartShortfall[] = [
  { pn: "PN-100", location: "JFK", shortage: 20, on_hand: 5, projected_demand: 25 },
  { pn: "PN-200", location: "LAX", shortage: 15, on_hand: 2, projected_demand: 17 },
  { pn: "PN-300", location: "ORD", shortage: 45, on_hand: 1, projected_demand: 50 },
];

function renderWithRouter(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe("TopShortagesTable", () => {
  it("renders one row per shortage with a Link to the Part Drill-Down", () => {
    renderWithRouter(<TopShortagesTable rows={rows} provenance={provenance} />);

    const link = screen.getByRole("link", { name: "PN-100" });
    expect(link).toHaveAttribute("href", "/parts/PN-100/JFK");
    expect(screen.getByText("JFK")).toBeInTheDocument();
  });

  it("encodeURIComponents pn/location in the link href", () => {
    renderWithRouter(
      <TopShortagesTable
        rows={[{ pn: "PN/100", location: "JFK A", shortage: 1, on_hand: 1, projected_demand: 1 }]}
        provenance={provenance}
      />,
    );

    const link = screen.getByRole("link", { name: "PN/100" });
    expect(link).toHaveAttribute("href", "/parts/PN%2F100/JFK%20A");
  });

  it("defaults to sorting by shortage descending", () => {
    renderWithRouter(<TopShortagesTable rows={rows} provenance={provenance} />);

    const dataRows = screen.getAllByRole("row").slice(1);
    const firstRowCells = within(dataRows[0]).getAllByRole("cell");
    expect(firstRowCells[0]).toHaveTextContent("PN-300"); // shortage 45, highest
  });

  it("re-sorts when a column header is clicked", async () => {
    const user = userEvent.setup();
    renderWithRouter(<TopShortagesTable rows={rows} provenance={provenance} />);

    await user.click(screen.getByRole("button", { name: /Part/i }));

    const dataRows = screen.getAllByRole("row").slice(1);
    const firstRowCells = within(dataRows[0]).getAllByRole("cell");
    expect(firstRowCells[0]).toHaveTextContent("PN-100"); // alpha asc
  });

  it("wraps numeric cells in a Metric with ProvChip (provenance invariant)", () => {
    renderWithRouter(<TopShortagesTable rows={rows} provenance={provenance} />);

    // 3 rows x 3 numeric columns (on_hand/shortage/projected_demand) = 9 ProvChips.
    expect(screen.getAllByTestId("prov-chip")).toHaveLength(9);
  });

  it("renders a caption and empty state when there are no rows", () => {
    renderWithRouter(<TopShortagesTable rows={[]} provenance={provenance} />);

    expect(screen.getByText("Full top-shortages list")).toBeInTheDocument();
    expect(screen.getByText("No shortages — nothing to prioritize right now.")).toBeInTheDocument();
  });
});
