/**
 * Provenance / MetricValue — the data-trust invariant.
 *
 * Mirrors docs/DATA-MODEL.md §1 "Provenance / MetricValue":
 *   Provenance = { source, systemOfRecord, freshnessAt, coverage, confidence, derived }
 *   MetricValue<T> = { value, provenance }
 *
 * The entire point of `MetricValue<T>` is that a raw value can never be
 * rendered as a metric without its lineage travelling with it — `Metric`
 * (src/components/Metric.tsx) only accepts `MetricValue<T>`, never a bare
 * `T`. That is enforced by the TypeScript type checker at compile time
 * (see src/lib/provenance.test-d.ts) and reinforced at runtime by
 * `isMetricValue` / `assertMetricValue` below for any boundary (API
 * responses, JSON, etc.) where the compiler can't see through the wire.
 */

/** Confidence/coverage traffic-light used by ProvChip — never color-only (WCAG). */
export type ProvenanceStatus = "good" | "warn" | "bad";

export interface Provenance {
  /** Human-readable origin, e.g. "eMRO Shop Floor". */
  source: string;
  /** Canonical system of record / FeedId, e.g. "INVENTORY", "eMRO". */
  systemOfRecord: string;
  /** ISO 8601 timestamp of when the underlying data was last refreshed. */
  freshnessAt: string;
  /** 0..1 — fraction of expected records/fields actually populated. */
  coverage: number;
  /** 0..1 — confidence in the value (lower for derived/forecast values). */
  confidence: number;
  /** True if this value is computed/derived rather than a direct feed read. */
  derived: boolean;
}

/** A value that can never appear in the UI without its provenance attached. */
export interface MetricValue<T> {
  value: T;
  provenance: Provenance;
}

/** Runtime guard — for values crossing an untyped boundary (JSON, storage, etc.). */
export function isProvenance(value: unknown): value is Provenance {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.source === "string" &&
    typeof v.systemOfRecord === "string" &&
    typeof v.freshnessAt === "string" &&
    typeof v.coverage === "number" &&
    typeof v.confidence === "number" &&
    typeof v.derived === "boolean"
  );
}

export function isMetricValue<T>(value: unknown): value is MetricValue<T> {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return "value" in v && isProvenance(v.provenance);
}

/** Throws if `value` is not a well-formed MetricValue — fail loudly, not silently. */
export function assertMetricValue<T>(value: unknown): asserts value is MetricValue<T> {
  if (!isMetricValue<T>(value)) {
    throw new TypeError(
      "Expected a MetricValue<T> ({ value, provenance }) — a metric cannot be rendered without provenance.",
    );
  }
}

/** Derives the ProvChip traffic-light status from confidence + coverage. */
export function provenanceStatus(provenance: Provenance): ProvenanceStatus {
  const score = Math.min(provenance.confidence, provenance.coverage);
  if (score >= 0.85) return "good";
  if (score >= 0.6) return "warn";
  return "bad";
}

/** Formats an ISO timestamp as a short "Nh ago" / "Nd ago" freshness label. */
export function formatFreshness(isoTimestamp: string, now: Date = new Date()): string {
  const then = new Date(isoTimestamp);
  const diffMs = now.getTime() - then.getTime();
  if (Number.isNaN(diffMs)) return "unknown";
  if (diffMs < 0) return "just now";

  const minutes = Math.round(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/** Convenience constructor — stamps a value with provenance in one call. */
export function withProvenance<T>(value: T, provenance: Provenance): MetricValue<T> {
  return { value, provenance };
}
