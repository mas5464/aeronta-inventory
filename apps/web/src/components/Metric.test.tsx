import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Metric } from "@/components/Metric";
import { withProvenance, type Provenance } from "@/lib/provenance";

const provenance: Provenance = {
  source: "eMRO Nightly Extract",
  systemOfRecord: "INVENTORY",
  freshnessAt: new Date().toISOString(),
  coverage: 1,
  confidence: 0.95,
  derived: true,
};

describe("Metric", () => {
  it("renders the formatted value alongside its ProvChip", () => {
    render(
      <Metric
        label="Parts"
        metric={withProvenance(21215, provenance)}
        format={(v) => v.toLocaleString("en-US")}
      />,
    );

    expect(screen.getByText("Parts")).toBeInTheDocument();
    expect(screen.getByText("21,215")).toBeInTheDocument();
    // A Metric can never render without its ProvChip — assert it's present.
    expect(screen.getByTestId("prov-chip")).toBeInTheDocument();
  });

  it("falls back to String(value) when no formatter is given", () => {
    render(<Metric metric={withProvenance(7, provenance)} />);
    expect(screen.getByText("7")).toBeInTheDocument();
  });
});
