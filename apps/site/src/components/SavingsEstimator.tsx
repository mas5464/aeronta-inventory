// apps/site/src/components/SavingsEstimator.tsx
//
// Homepage island (client:visible). Two sliders → an illustrative annual
// savings band computed by src/lib/estimator.ts. Astro server-renders the
// default state, so no-JS visitors still see a complete worked example and
// the assumption text; only the sliders need hydration.
import { useState } from "react";
import { ASSUMPTIONS, estimateSavings, formatUsd, pct, perKey } from "../lib/estimator";

const DEFAULT_KEYS = 25_000;
const DEFAULT_VALUE = 50_000_000;

export function SavingsEstimator() {
  const [keys, setKeys] = useState(DEFAULT_KEYS);
  const [value, setValue] = useState(DEFAULT_VALUE);
  const band = estimateSavings(value);
  const per = perKey(band, keys);

  return (
    <div className="rounded-card border bg-muted p-6 sm:p-8">
      <div className="grid gap-8 md:grid-cols-2">
        <div className="space-y-6">
          <label className="block">
            <span className="flex justify-between text-sm">
              <span className="font-medium">Part–location keys</span>
              <span className="text-muted-foreground">{keys.toLocaleString("en-US")}</span>
            </span>
            <input
              type="range"
              min={1_000}
              max={100_000}
              step={1_000}
              value={keys}
              onChange={(e) => setKeys(Number(e.target.value))}
              className="mt-2 w-full accent-coral"
              aria-label="Part-location keys"
            />
          </label>
          <label className="block">
            <span className="flex justify-between text-sm">
              <span className="font-medium">On-hand inventory value</span>
              <span className="text-muted-foreground">{formatUsd(value)}</span>
            </span>
            <input
              type="range"
              min={1_000_000}
              max={500_000_000}
              step={1_000_000}
              value={value}
              onChange={(e) => setValue(Number(e.target.value))}
              className="mt-2 w-full accent-coral"
              aria-label="On-hand inventory value in dollars"
            />
          </label>
          <p className="text-xs leading-relaxed text-muted-foreground" data-testid="assumptions">
            Illustrative model — annual holding cost at {pct(ASSUMPTIONS.holdingRateLow)}–
            {pct(ASSUMPTIONS.holdingRateHigh)}% of on-hand value, with governed optimization
            reducing excess on-hand value by {pct(ASSUMPTIONS.reductionLow)}–
            {pct(ASSUMPTIONS.reductionHigh)}%. Your real number comes from the Business Value
            Report, attributed to specific applied changes.
          </p>
        </div>
        <div className="flex flex-col justify-center rounded-card bg-panel p-6 text-background">
          <div className="text-xs uppercase tracking-wide text-panel-muted">
            Illustrative annual savings
          </div>
          <div className="mt-2 text-3xl font-medium tracking-headline" data-testid="savings-band">
            {formatUsd(band.low)} – {formatUsd(band.high)}
          </div>
          <div className="mt-2 text-sm text-panel-muted" data-testid="per-key">
            ≈ {formatUsd(per.low)}–{formatUsd(per.high)} per key across{" "}
            {keys.toLocaleString("en-US")} keys
          </div>
        </div>
      </div>
    </div>
  );
}
