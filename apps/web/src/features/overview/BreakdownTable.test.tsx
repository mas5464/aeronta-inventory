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

// --- search wiring (the F1 primitive's searchAccessor, previously unused) --- //
const manyRows: Breakdown[] = Array.from({ length: 20 }, (_, i) => ({
  key: String(21 + i), // ATA-chapter-like keys "21".."40"
  count: 100 + i,
  on_hand: 1000 + i,
  shortage: i,
}));

describe("BreakdownTable search", () => {
  it("renders a search input only when the breakdown clears the row threshold", () => {
    const { rerender } = render(
      <BreakdownTable rows={manyRows} rowNoun="ATA chapter" provenance={provenance} />,
    );
    expect(screen.getByRole("textbox", { name: /Search/i })).toBeInTheDocument();

    rerender(<BreakdownTable rows={rows} rowNoun="ATA chapter" provenance={provenance} />);
    expect(screen.queryByRole("textbox", { name: /Search/i })).not.toBeInTheDocument();
  });

  it("narrows rows by the DISPLAYED label (labelFor), keeping the active sort", async () => {
    const user = userEvent.setup();
    render(
      <BreakdownTable
        rows={manyRows}
        rowNoun="ATA chapter"
        labelFor={(key) => `ATA ${key}`}
        provenance={provenance}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: /Search/i }), "ATA 3");

    const dataRows = screen.getAllByRole("row").slice(1);
    // "ATA 3" prefix-matches ATA 30..39 = 10 rows (raw keys alone would not match).
    expect(dataRows).toHaveLength(10);
    // Default sort (shortage desc) still applies within the narrowed set.
    expect(within(dataRows[0]).getAllByRole("cell")[0]).toHaveTextContent("ATA 39");
  });

  it("shows a no-match message naming the query instead of the no-data empty state", async () => {
    const user = userEvent.setup();
    render(<BreakdownTable rows={manyRows} rowNoun="ATA chapter" provenance={provenance} />);

    await user.type(screen.getByRole("textbox", { name: /Search/i }), "zzz");

    expect(screen.getByText('No ATA chapter rows match "zzz".')).toBeInTheDocument();
    expect(
      screen.queryByText("No ATA chapter breakdown data available."),
    ).not.toBeInTheDocument();
  });
});
