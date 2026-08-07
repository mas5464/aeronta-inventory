import type { Provenance } from "@/lib/provenance";
import type { ScenarioSolveResult } from "@/lib/api/types";

/**
 * Provenance stamp for What-If Scenario solve results (PRD §6.5).
 *
 * `ScenarioSolveResult` is computed live by the BFF's `ScenarioSolver`
 * (services/agent-spine/src/trax_io_spine/bff/scenario.py) from the same real
 * eMRO-extract-derived per-key demand/lead-time/cost primitives the rest of the BFF
 * uses — it is a derived projection, not a direct feed read, and it mirrors (rather
 * than re-runs) the real policy engine's normal-approximation safety-stock math. No
 * Provenance is supplied by the BFF from the source manifest and scored-key
 * coverage. Legacy saved results intentionally degrade to unknown freshness
 * and zero confidence/coverage; the browser never substitutes render time.
 */
export function scenarioProvenance(
  result: ScenarioSolveResult,
): Provenance {
  const sourceAsOf = result.source_as_of;
  const freshnessAt = sourceAsOf
    ? /^\d{4}-\d{2}-\d{2}$/.test(sourceAsOf)
      ? `${sourceAsOf}T00:00:00.000Z`
      : sourceAsOf
    : "";
  const coverage = Math.max(
    0,
    Math.min(1, result.source_coverage ?? 0),
  );
  const confidence = Math.max(
    0,
    Math.min(1, result.source_confidence ?? 0),
  );
  return {
    source: "Scenario Solver (derived from eMRO extract)",
    systemOfRecord: "INVENTORY",
    freshnessAt,
    coverage,
    confidence,
    derived: true,
  };
}
