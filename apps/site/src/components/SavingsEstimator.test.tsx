// apps/site/src/components/SavingsEstimator.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SavingsEstimator } from "./SavingsEstimator";

describe("SavingsEstimator", () => {
  it("renders the default band for 25k keys / $50M on-hand", () => {
    render(<SavingsEstimator />);
    // 50M × 0.08 × 0.18 = 720,000 ; 50M × 0.15 × 0.25 = 1,875,000
    expect(screen.getByTestId("savings-band")).toHaveTextContent("$720,000 – $1,875,000");
  });

  it("recomputes the band when the on-hand value slider moves", () => {
    render(<SavingsEstimator />);
    fireEvent.change(screen.getByLabelText("On-hand inventory value in dollars"), {
      target: { value: "10000000" },
    });
    // 10M × 0.08 × 0.18 = 144,000 ; 10M × 0.15 × 0.25 = 375,000
    expect(screen.getByTestId("savings-band")).toHaveTextContent("$144,000 – $375,000");
  });

  it("recomputes the per-key line when the keys slider moves", () => {
    render(<SavingsEstimator />);
    fireEvent.change(screen.getByLabelText("Part-location keys"), {
      target: { value: "10000" },
    });
    // 720,000 / 10,000 = 72 ; 1,875,000 / 10,000 ≈ 188 (rounded by formatUsd)
    expect(screen.getByTestId("per-key")).toHaveTextContent("$72–$188 per key");
  });

  it("shows the assumption set verbatim (honesty rule)", () => {
    render(<SavingsEstimator />);
    const note = screen.getByTestId("assumptions");
    expect(note).toHaveTextContent("18–25%");
    expect(note).toHaveTextContent("8–15%");
    expect(note).toHaveTextContent("Business Value Report");
  });
});
