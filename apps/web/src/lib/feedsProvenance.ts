import type { Provenance } from "@/lib/provenance";

/**
 * Provenance stamp for the Data & Connections view (PRD §6.7).
 *
 * The BFF's `FeedsSummary` (services/agent-spine/src/trax_io_spine/bff/store.py
 * `PlannerStore.feeds_summary()`) has no per-field provenance on the wire, so — as
 * with `forecastProvenance.ts` — we honestly stamp the whole surface with its true
 * origin: the nightly extract's `manifest.json`, cross-referenced against the static,
 * code-verified feed->domain mapping in the BFF's `bff/feeds.py`. Confidence is high
 * but not 1.0 because `rows`/`last_sync` are best-effort (None when the manifest
 * lacks per-domain row counts, or has no manifest at all) — the status/domains/notes
 * columns themselves are exact, derived directly from source code, not a measurement
 * with the freshness/coverage semantics the rest of the provenance model assumes.
 */
export function feedsProvenance(asOf: Date = new Date()): Provenance {
  return {
    source: "Extract manifest (eMRO)",
    systemOfRecord: "EXTRACT_MANIFEST",
    freshnessAt: asOf.toISOString(),
    coverage: 1,
    confidence: 0.9,
    derived: true,
  };
}
