// apps/site/src/components/WorkbenchDemo.tsx
//
// Hero island (client:load): a synthetic approval-queue panel in the parent
// site's dark-mockup grammar. The visitor approves one governed
// recommendation and watches the write land — status flips, an append-only
// ledger entry appears with before/after values, and the projected-savings
// counter ticks up. Reset restarts it.
//
// Every value here is hardcoded synthetic data, disclosed in the panel
// chrome ("TRAX eMRO · synthetic demo") exactly like aeronta.com labels its
// own demo panels. No real tenant, part, or dollar figure appears.
import { useRef, useState } from "react";
import { formatUsd } from "../lib/estimator";

const REC = {
  pn: "3290-45-11",
  description: "Fuel shutoff valve",
  location: "MIA",
  tier: "B",
  current: { rop: 6, eoq: 12, ss: 4, max: 18 },
  recommended: { rop: 3, eoq: 5, ss: 2, max: 8 },
  annualSavingUsd: 9_120,
  reason:
    "24 months of demand support a lower reorder point — excess on-hand value is carrying avoidable holding cost.",
} as const;

// jsdom (tests) has no matchMedia; treat that as reduced motion so the
// counter is deterministic. Real browsers report the user's preference.
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const VALUE_ROWS = [
  { label: "ROP", from: REC.current.rop, to: REC.recommended.rop },
  { label: "EOQ", from: REC.current.eoq, to: REC.recommended.eoq },
  { label: "SS", from: REC.current.ss, to: REC.recommended.ss },
  { label: "Max", from: REC.current.max, to: REC.recommended.max },
] as const;

export function WorkbenchDemo() {
  const [written, setWritten] = useState(false);
  const [savings, setSavings] = useState(0);
  const rafRef = useRef(0);

  function approve() {
    setWritten(true);
    if (prefersReducedMotion()) {
      setSavings(REC.annualSavingUsd);
      return;
    }
    const start = performance.now();
    const DURATION = 800;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / DURATION);
      setSavings(Math.round(REC.annualSavingUsd * t));
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }

  function reset() {
    cancelAnimationFrame(rafRef.current);
    setWritten(false);
    setSavings(0);
  }

  return (
    <div className="overflow-hidden rounded-card bg-panel text-background shadow-xl">
      <div className="flex items-center justify-between border-b border-panel-line px-5 py-3 text-xs">
        <span className="font-medium">Demo Air · Materials planning</span>
        <span className="text-panel-muted">TRAX eMRO · synthetic demo</span>
      </div>

      <div className="space-y-4 p-5">
        <div className="rounded-lg border border-panel-line p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="font-mono text-sm">{REC.pn}</div>
              <div className="text-xs text-panel-muted">
                {REC.description} · {REC.location}
              </div>
            </div>
            {written ? (
              <span className="rounded-full bg-mint px-2.5 py-1 text-xs font-medium text-forest">
                Written to eMRO
              </span>
            ) : (
              <span className="rounded-full bg-sun/20 px-2.5 py-1 text-xs font-medium text-sun">
                Pending approval
              </span>
            )}
          </div>

          <dl className="mt-4 grid grid-cols-4 gap-2 text-center">
            {VALUE_ROWS.map((row) => (
              <div key={row.label} className="rounded-md bg-background/5 p-2">
                <dt className="text-[10px] uppercase tracking-wide text-panel-muted">
                  {row.label}
                </dt>
                <dd className="mt-1 text-sm">
                  <span className="text-panel-muted line-through">{row.from}</span>{" "}
                  <span className="font-medium text-peach">{row.to}</span>
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-3 text-xs leading-relaxed text-panel-muted">{REC.reason}</p>

          <div className="mt-4 flex items-center justify-between gap-3">
            <div className="text-xs text-panel-muted">
              Tier {REC.tier} · projected {formatUsd(REC.annualSavingUsd)}/yr
            </div>
            {written ? (
              <button
                type="button"
                onClick={reset}
                className="text-xs text-panel-muted underline-offset-2 hover:underline"
              >
                Reset demo
              </button>
            ) : (
              <button
                type="button"
                onClick={approve}
                className="rounded-full bg-peach px-4 py-1.5 text-xs font-semibold text-panel transition-opacity hover:opacity-90"
              >
                Approve
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between rounded-lg border border-panel-line px-4 py-3">
          <span className="text-xs text-panel-muted">Projected annual savings unlocked</span>
          <span className="text-lg font-medium text-peach" data-testid="savings-counter">
            {formatUsd(savings)}
          </span>
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wide text-panel-muted">
            Audit ledger · append-only
          </div>
          {written ? (
            <div
              className="mt-2 rounded-lg border border-panel-line px-4 py-3 text-xs leading-relaxed"
              data-testid="ledger-entry"
            >
              <span className="font-mono">{REC.pn}</span> @ {REC.location} — ROP{" "}
              {REC.current.rop}→{REC.recommended.rop} · EOQ {REC.current.eoq}→
              {REC.recommended.eoq} · SS {REC.current.ss}→{REC.recommended.ss} · Max{" "}
              {REC.current.max}→{REC.recommended.max}
              <div className="mt-1 text-panel-muted">
                principal: planner · rollback available · just now
              </div>
            </div>
          ) : (
            <p className="mt-2 text-xs text-panel-muted">
              Approve the recommendation to see the write land here — with before/after
              values and a rollback path.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
