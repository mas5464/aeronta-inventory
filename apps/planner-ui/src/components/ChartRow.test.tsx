import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SAMPLE_SEED } from "../api/sample";
import { ChartRow } from "./ChartRow";

const ROWS = SAMPLE_SEED.map((e) => e.row);

describe("ChartRow", () => {
  it("summarizes the queue by type and by tier", () => {
    render(<ChartRow rows={ROWS} />);
    expect(screen.getByText("By type")).toBeInTheDocument();
    expect(screen.getByText("By tier")).toBeInTheDocument();
    // donut center shows the total, and the type legend lists each type
    expect(screen.getByLabelText("Recommendations by type")).toBeInTheDocument();
    expect(screen.getByText(/^transfer$/i)).toBeInTheDocument();
    expect(screen.getByText(/^reduce stock$/i)).toBeInTheDocument();
    // tier rows are present
    expect(screen.getByText("Tier A")).toBeInTheDocument();
  });
});
