// apps/site/src/lib/estimator.test.ts
import { describe, expect, it } from "vitest";
import { ASSUMPTIONS, estimateSavings, formatUsd, pct, perKey } from "./estimator";

describe("estimateSavings", () => {
  it("returns the low×low / high×high band of reduction × holding rate", () => {
    const band = estimateSavings(10_000_000);
    expect(band.low).toBeCloseTo(10_000_000 * 0.08 * 0.18, 5); // 144,000
    expect(band.high).toBeCloseTo(10_000_000 * 0.15 * 0.25, 5); // 375,000
  });

  it("is linear in on-hand value", () => {
    const one = estimateSavings(1_000_000);
    const five = estimateSavings(5_000_000);
    expect(five.low).toBeCloseTo(one.low * 5, 5);
    expect(five.high).toBeCloseTo(one.high * 5, 5);
  });

  it("clamps negative input to a zero band", () => {
    expect(estimateSavings(-5)).toEqual({ low: 0, high: 0 });
  });

  it("keeps low <= high for any non-negative input", () => {
    for (const v of [0, 1, 1_000_000, 500_000_000]) {
      const band = estimateSavings(v);
      expect(band.low).toBeLessThanOrEqual(band.high);
    }
  });
});

describe("perKey", () => {
  it("divides the band by the key count", () => {
    const per = perKey({ low: 144_000, high: 375_000 }, 1_000);
    expect(per.low).toBeCloseTo(144, 5);
    expect(per.high).toBeCloseTo(375, 5);
  });

  it("returns a zero band for zero or negative keys", () => {
    expect(perKey({ low: 100, high: 200 }, 0)).toEqual({ low: 0, high: 0 });
    expect(perKey({ low: 100, high: 200 }, -3)).toEqual({ low: 0, high: 0 });
  });
});

describe("formatUsd", () => {
  it("formats whole dollars, no cents", () => {
    expect(formatUsd(144_000)).toBe("$144,000");
    expect(formatUsd(0)).toBe("$0");
  });

  it("rounds fractional dollars", () => {
    expect(formatUsd(1234.56)).toBe("$1,235");
  });
});

describe("pct", () => {
  it("converts a rate to an integer percentage without float dust", () => {
    expect(pct(ASSUMPTIONS.holdingRateLow)).toBe(18);
    expect(pct(ASSUMPTIONS.holdingRateHigh)).toBe(25);
    expect(pct(ASSUMPTIONS.reductionLow)).toBe(8);
    expect(pct(ASSUMPTIONS.reductionHigh)).toBe(15);
  });
});
