import { describe, expect, it } from "vitest";
import { formatAmount, formatRatePct, savingsComponentLabel } from "@/features/reports/reportView";

describe("savingsComponentLabel", () => {
  it("maps the known savings component keys to human labels", () => {
    expect(savingsComponentLabel("holding_cost_delta")).toBe("Holding cost");
    expect(savingsComponentLabel("ordering_cost_delta")).toBe("Ordering cost");
    expect(savingsComponentLabel("stockout_risk_delta")).toBe("Stockout risk");
  });

  it("title-cases an unknown snake_case key as a fallback (never renders it raw)", () => {
    expect(savingsComponentLabel("some_new_component")).toBe("Some New Component");
  });
});

describe("formatRatePct", () => {
  it("renders a 0-1 rate as a one-decimal percentage", () => {
    expect(formatRatePct(0.5)).toBe("50.0%");
    expect(formatRatePct(0.25)).toBe("25.0%");
    expect(formatRatePct(0)).toBe("0.0%");
  });
});

describe("formatAmount", () => {
  it("prefixes the server-formatted Decimal string with $ (no float parsing)", () => {
    expect(formatAmount("1250.00")).toBe("$1250.00");
    expect(formatAmount("-0.06")).toBe("$-0.06");
  });
});
