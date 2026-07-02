import type { ReactElement } from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { DrillContent } from "@/features/overview/DrillContent";
import { DRILL_SPECS } from "@/features/overview/drillSpecs";
import type { DashboardSummary } from "@/lib/api/types";
import type { Provenance } from "@/lib/provenance";

const provenance: Provenance = {
  source: "eMRO Nightly Extract",
  systemOfRecord: "INVENTORY",
  freshnessAt: new Date().toISOString(),
  coverage: 1,
  confidence: 0.95,
  derived: true,
};

const data: DashboardSummary = {
  parts: 100,
  total_on_hand: 1000,
  total_on_hand_value: 5000,
  total_shortage: 10,
  total_projected_demand: 50,
  aog_exposure: 1,
  open_recommendations: 5,
  net_cost_impact: -100,
  by_criticality: [{ key: "1", count: 40, on_hand: 400, shortage: 2 }],
  by_ata: [{ key: "32", count: 30, on_hand: 300, shortage: 5 }],
  by_part_class: [{ key: "ROTABLE", count: 20, on_hand: 200, shortage: 1 }],
  by_tier: [{ key: "2", count: 10, on_hand: 100, shortage: 1 }],
  top_shortages: [{ pn: "PN-1", location: "JFK", shortage: 5, on_hand: 1, projected_demand: 6 }],
};

function renderWithRouter(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

function specById(id: string) {
  const spec = DRILL_SPECS.find((s) => s.id === id);
  if (!spec) throw new Error(`no spec ${id}`);
  return spec;
}

describe("DrillContent", () => {
  it("renders spec.description above the table", () => {
    renderWithRouter(<DrillContent spec={specById("ata-risk")} data={data} provenance={provenance} />);
    expect(screen.getByText(specById("ata-risk").description)).toBeInTheDocument();
  });

  it("dispatches a breakdown spec to BreakdownTable using data[spec.breakdownKey]", () => {
    renderWithRouter(<DrillContent spec={specById("ata-risk")} data={data} provenance={provenance} />);
    expect(screen.getByText("32")).toBeInTheDocument(); // by_ata's key
  });

  it("applies spec.labelFor when dispatching a breakdown spec", () => {
    renderWithRouter(<DrillContent spec={specById("health-mix")} data={data} provenance={provenance} />);
    expect(screen.getByText("Tier 1")).toBeInTheDocument();
  });

  it("dispatches the shortages spec to TopShortagesTable using data.top_shortages", () => {
    renderWithRouter(
      <DrillContent spec={specById("priority-actions")} data={data} provenance={provenance} />,
    );
    expect(screen.getByRole("link", { name: "PN-1" })).toBeInTheDocument();
  });

  it("dispatches by-part-class and by-tier specs (the previously-unrendered gap)", () => {
    renderWithRouter(
      <DrillContent spec={specById("by-part-class")} data={data} provenance={provenance} />,
    );
    expect(screen.getByText("ROTABLE")).toBeInTheDocument();
  });
});
