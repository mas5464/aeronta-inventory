import type { Provenance } from "@/lib/provenance";

/**
 * Provenance stamp for What-If Scenario solve results (PRD §6.5).
 *
 * `ScenarioSolveResult` is computed live by the BFF's `ScenarioSolver`
 * (services/agent-spine/src/trax_io_spine/bff/scenario.py) from the same real
 * eMRO-extract-derived per-key demand/lead-time/cost primitives the rest of the BFF
 * uses — it is a derived projection, not a direct feed read, and it mirrors (rather
 * than re-runs) the real policy engine's normal-approximation safety-stock math. No
 * per-field provenance travels on the wire, so — as with `dashboardProvenance.ts` — we
 * honestly stamp the whole result with its true origin and a confidence reduced to
 * reflect that it is a policy-lever projection, not a committed plan.
 */
export function scenarioProvenance(asOf: Date = new Date()): Provenance {
  return {
    source: "Scenario Solver (derived from eMRO extract)",
    systemOfRecord: "INVENTORY",
    freshnessAt: asOf.toISOString(),
    coverage: 1,
    confidence: 0.85,
    derived: true,
  };
}
