import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProvChip } from "@/components/ProvChip";
import type { Provenance } from "@/lib/provenance";

const provenance: Provenance = {
  source: "eMRO Nightly Extract",
  systemOfRecord: "INVENTORY",
  freshnessAt: new Date().toISOString(),
  coverage: 1,
  confidence: 0.95,
  derived: true,
};

describe("ProvChip", () => {
  it("renders the source and a status affordance that isn't color-only", () => {
    render(<ProvChip provenance={provenance} />);

    const chip = screen.getByTestId("prov-chip");
    expect(chip).toHaveAttribute("data-status", "good");
    expect(chip).toHaveTextContent("eMRO Nightly Extract");
    // WCAG: status must be conveyed via text (aria-label), not color alone.
    expect(chip.getAttribute("aria-label")).toMatch(/confidence/i);
  });

  it("downgrades to warn/bad status for lower confidence or coverage", () => {
    render(<ProvChip provenance={{ ...provenance, confidence: 0.4, coverage: 0.4 }} />);
    expect(screen.getByTestId("prov-chip")).toHaveAttribute("data-status", "bad");
  });
});
