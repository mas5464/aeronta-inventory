// apps/site/src/lib/estimator.ts
//
// Pure math behind the homepage SavingsEstimator island. The output is an
// ILLUSTRATIVE BAND, never a point estimate — a real operator's number comes
// from the Business Value Report after governed changes are applied.
//
// Assumption set (rendered verbatim next to the sliders on the page):
//   - Annual holding cost runs 18–25% of on-hand inventory value
//     (industry-typical range for aviation spares).
//   - Governed optimization typically reduces excess on-hand value by 8–15%.
// Band = on-hand value × reduction × holding rate, pairing low×low and
// high×high so the band brackets the assumption space.

export const ASSUMPTIONS = {
  holdingRateLow: 0.18,
  holdingRateHigh: 0.25,
  reductionLow: 0.08,
  reductionHigh: 0.15,
} as const;

export type SavingsBand = { low: number; high: number };

export function estimateSavings(onHandValueUsd: number): SavingsBand {
  const v = Math.max(0, onHandValueUsd);
  return {
    low: v * ASSUMPTIONS.reductionLow * ASSUMPTIONS.holdingRateLow,
    high: v * ASSUMPTIONS.reductionHigh * ASSUMPTIONS.holdingRateHigh,
  };
}

export function perKey(band: SavingsBand, keys: number): SavingsBand {
  if (keys <= 0) return { low: 0, high: 0 };
  return { low: band.low / keys, high: band.high / keys };
}

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export function formatUsd(n: number): string {
  return usd.format(n);
}

// 0.18 * 100 === 18.000000000000004 in IEEE 754 — round for display.
export function pct(x: number): number {
  return Math.round(x * 100);
}
