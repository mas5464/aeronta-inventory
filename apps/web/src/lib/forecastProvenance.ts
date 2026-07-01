import type { Provenance } from "@/lib/provenance";

/**
 * Provenance stamps for the Forecast & Service Levels view (PRD §6.6).
 *
 * The BFF's `ForecastSummary` (services/agent-spine/src/trax_io_spine/bff/store.py
 * `PlannerStore.forecast_summary()`) has no per-field provenance on the wire, so — as
 * with `dashboardProvenance.ts` — we honestly stamp each section with its true origin:
 *
 * - `servicLevelProvenance`: the differentiated SL *targets* come straight from
 *   onboarding config (`TenantPolicyConfig.service_level_by_tier`), not a data feed —
 *   confidence/coverage are 1.0 because it's a policy setting, not a measurement. SKU
 *   counts and the coverage proxy are real eMRO extract aggregates.
 * - `methodCoverageProvenance`: real regime classification computed from
 *   DEMAND_HISTORY via the engine's deterministic classifier.
 * - `accuracyProvenance`: same DEMAND_HISTORY origin, but explicitly a derived proxy,
 *   not a backtest — lower confidence to reflect that honestly.
 */
export function serviceLevelProvenance(asOf: Date = new Date()): Provenance {
  return {
    source: "Tenant Policy Config",
    systemOfRecord: "TENANT_POLICY_CONFIG",
    freshnessAt: asOf.toISOString(),
    coverage: 1,
    confidence: 1,
    derived: false,
  };
}

export function methodCoverageProvenance(asOf: Date = new Date()): Provenance {
  return {
    source: "eMRO Nightly Extract",
    systemOfRecord: "DEMAND_HISTORY",
    freshnessAt: asOf.toISOString(),
    coverage: 1,
    confidence: 0.95,
    derived: true,
  };
}

export function accuracyProvenance(asOf: Date = new Date()): Provenance {
  return {
    source: "eMRO Nightly Extract",
    systemOfRecord: "DEMAND_HISTORY",
    freshnessAt: asOf.toISOString(),
    coverage: 1,
    confidence: 0.6,
    derived: true,
  };
}
