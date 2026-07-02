import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { BreakdownTable } from "@/features/overview/BreakdownTable";
import type { Breakdown } from "@/lib/api/types";
import type { Provenance } from "@/lib/provenance";

const provenance: Provenance = {
  source: "eMRO Nightly Extract",
  systemOfRecord: "INVENTORY",
  freshnessAt: new Date().toISOString(),
  coverage: 1,
  confidence: 0.95,
  derived: true,
};

const rows: Breakdown[] = [
  { key: "1", count: 4000, on_hand: 40000, shortage: 20 },
  { key: "2", count: 7000, on_hand: 35000, shortage: 60 },
  { key: "3", count: 5900, on_hand: 25000, shortage: 40 },
];

describe("BreakdownTable", () => {
  it("renders one row per breakdown entry with label/count/on-hand/shortage", () => {
    render(<BreakdownTable rows={rows} rowNoun="criticality tier" provenance={provenance} />);

    const dataRows = screen.getAllByRole("row").slice(1); // drop header row
    expect(dataRows).toHaveLength(3);
    expect(screen.getByText("40,000")).toBeInTheDocument();
    expect(screen.getByText("7,000")).toBeInTheDocument();
  });

  it("applies labelFor to the label column", () => {
    render(
      <BreakdownTable
        rows={rows}
        rowNoun="criticality tier"
        labelFor={(key) => `Tier ${key}`}
        provenance={provenance}
      />,
    );
    expect(screen.getByText("Tier 1")).toBeInTheDocument();
    expect(screen.getByText("Tier 2")).toBeInTheDocument();
  });

  it("defaults to sorting by shortage descending", () => {
    render(<BreakdownTable rows={rows} rowNoun="criticality tier" provenance={provenance} />);

    const dataRows = screen.getAllByRole("row").slice(1);
    const firstRowCells = within(dataRows[0]).getAllByRole("cell");
    // key "2" has the highest shortage (60) — should render first.
    expect(firstRowCells[0]).toHaveTextContent("2");
  });

  it("re-sorts when a column header is clicked", async () => {
    const user = userEvent.setup();
    render(<BreakdownTable rows={rows} rowNoun="criticality tier" provenance={provenance} />);

    await user.click(screen.getByRole("button", { name: /On-hand/i }));

    const dataRows = screen.getAllByRole("row").slice(1);
    const firstRowCells = within(dataRows[0]).getAllByRole("cell");
    // Clicking a fresh numeric column defaults to desc — highest on_hand (40000, key "1") first.
    expect(firstRowCells[0]).toHaveTextContent("1");
  });

  it("wraps every numeric cell in a Metric with a ProvChip (provenance invariant)", () => {
    render(<BreakdownTable rows={rows} rowNoun="criticality tier" provenance={provenance} />);

    // 3 rows x 3 numeric columns (count/on_hand/shortage) = 9 ProvChips.
    expect(screen.getAllByTestId("prov-chip")).toHaveLength(9);
  });

  it("renders a caption and empty state when there are no rows", () => {
    render(<BreakdownTable rows={[]} rowNoun="ATA chapter" provenance={provenance} />);

    expect(screen.getByText("Full breakdown by ATA chapter")).toBeInTheDocument();
    expect(screen.getByText("No ATA chapter breakdown data available.")).toBeInTheDocument();
  });
});
