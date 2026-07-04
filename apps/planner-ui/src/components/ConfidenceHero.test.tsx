import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { EvidenceView } from "../api/types";
import { ConfidenceHero } from "./ConfidenceHero";

const EVIDENCE: EvidenceView[] = [
  { kind: "open_order", ref_id: "ev-1", detail: "Order 3 due 2026-05-04", as_of: null },
  { kind: "demand_history", ref_id: "ev-2", detail: "14 removals / 24mo", as_of: null },
];

describe("ConfidenceHero", () => {
  it("renders the confidence percentage, reason, and findings", () => {
    render(
      <ConfidenceHero
        reason="Tier A — essentiality 1."
        confidenceScore={0.91}
        evidence={EVIDENCE}
        status="pending"
      />,
    );
    expect(screen.getByText("91%")).toBeInTheDocument();
    expect(screen.getByText("Tier A — essentiality 1.")).toBeInTheDocument();
    expect(screen.getByText("Key findings")).toBeInTheDocument();
    expect(screen.getByText(/Order 3 due 2026-05-04/)).toBeInTheDocument();
    expect(screen.getByText("open order")).toBeInTheDocument(); // typeLabel(kind)
  });

  it("omits the findings section entirely when evidence is empty", () => {
    render(
      <ConfidenceHero
        reason="No supporting evidence yet."
        confidenceScore={0.6}
        evidence={[]}
        status="pending"
      />,
    );
    expect(screen.queryByText("Key findings")).not.toBeInTheDocument();
    expect(screen.getByText("No supporting evidence yet.")).toBeInTheDocument();
  });

  it("colors the percentage by tier", () => {
    const { rerender } = render(
      <ConfidenceHero reason="r" confidenceScore={0.9} evidence={[]} status="pending" />,
    );
    expect(screen.getByText("90%").className).toContain("confHigh");

    rerender(<ConfidenceHero reason="r" confidenceScore={0.6} evidence={[]} status="pending" />);
    expect(screen.getByText("60%").className).toContain("confMedium");

    rerender(<ConfidenceHero reason="r" confidenceScore={0.3} evidence={[]} status="pending" />);
    expect(screen.getByText("30%").className).toContain("confLow");
  });

  it("shows a 'Why this recommendation?' heading above the reason", () => {
    render(
      <ConfidenceHero reason="Some reason text." confidenceScore={0.5} evidence={[]} status="pending" />,
    );
    expect(screen.getByText("Why this recommendation?")).toBeInTheDocument();
  });

  it("shows the AI Recommendation header with an icon and subtitle", () => {
    render(<ConfidenceHero reason="r" confidenceScore={0.5} evidence={[]} status="pending" />);
    expect(screen.getByText("AI Recommendation")).toBeInTheDocument();
    expect(screen.getByText("Powered by predictive analytics")).toBeInTheDocument();
  });

  it("shows no status badge for a pending recommendation", () => {
    render(<ConfidenceHero reason="r" confidenceScore={0.5} evidence={[]} status="pending" />);
    expect(screen.queryByText("approved")).not.toBeInTheDocument();
    expect(screen.queryByText("rejected")).not.toBeInTheDocument();
    expect(screen.queryByText("deferred")).not.toBeInTheDocument();
  });

  it("shows the status badge for a decided recommendation", () => {
    render(<ConfidenceHero reason="r" confidenceScore={0.5} evidence={[]} status="approved" />);
    expect(screen.getByText("approved").className).toContain("status_approved");
  });
});
