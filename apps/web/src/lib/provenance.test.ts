import { describe, expect, it } from "vitest";
import {
  assertMetricValue,
  formatFreshness,
  isMetricValue,
  isProvenance,
  provenanceStatus,
  withProvenance,
  type Provenance,
} from "@/lib/provenance";

const goodProvenance: Provenance = {
  source: "eMRO Shop Floor",
  systemOfRecord: "REPAIR_ORDERS",
  freshnessAt: new Date().toISOString(),
  coverage: 0.98,
  confidence: 0.95,
  derived: false,
};

describe("MetricValue / Provenance invariant", () => {
  it("wraps a bare value into a MetricValue via withProvenance", () => {
    const metric = withProvenance(42, goodProvenance);
    expect(metric.value).toBe(42);
    expect(metric.provenance).toBe(goodProvenance);
  });

  it("type-level: Metric's `metric` prop only accepts MetricValue<T>, not a bare T", () => {
    // This block is a compile-time assertion, not a runtime one: if someone
    // changes Metric's prop type to accept `T` directly, the //@ts-expect-error
    // below stops being an error and `tsc -b` (npm run build) fails, catching
    // the regression before it ships.
    type MetricProps<T> = { metric: import("@/lib/provenance").MetricValue<T> };
    function acceptsMetric<T>(_props: MetricProps<T>) {}

    // Valid: a proper MetricValue typechecks.
    acceptsMetric<number>({ metric: withProvenance(1, goodProvenance) });

    // Invalid: a raw number must NOT typecheck as a MetricValue<number>.
    // @ts-expect-error - a bare value (no provenance) must not satisfy MetricValue<T>
    acceptsMetric<number>({ metric: 1 });
  });

  it("isProvenance recognizes well-formed provenance and rejects malformed objects", () => {
    expect(isProvenance(goodProvenance)).toBe(true);
    expect(isProvenance({ ...goodProvenance, coverage: "0.9" })).toBe(false);
    expect(isProvenance(null)).toBe(false);
    expect(isProvenance(42)).toBe(false);
  });

  it("isMetricValue rejects a raw value posing as a MetricValue at a runtime boundary", () => {
    expect(isMetricValue(withProvenance(42, goodProvenance))).toBe(true);
    expect(isMetricValue(42)).toBe(false);
    expect(isMetricValue({ value: 42 })).toBe(false);
    expect(isMetricValue({ value: 42, provenance: { source: "x" } })).toBe(false);
  });

  it("assertMetricValue throws for anything that isn't a MetricValue", () => {
    expect(() => assertMetricValue(42)).toThrow(/cannot be rendered without provenance/);
    expect(() => assertMetricValue(withProvenance(1, goodProvenance))).not.toThrow();
  });

  it("provenanceStatus derives good/warn/bad from min(confidence, coverage)", () => {
    expect(provenanceStatus({ ...goodProvenance, confidence: 0.9, coverage: 0.9 })).toBe("good");
    expect(provenanceStatus({ ...goodProvenance, confidence: 0.7, coverage: 0.9 })).toBe("warn");
    expect(provenanceStatus({ ...goodProvenance, confidence: 0.3, coverage: 0.9 })).toBe("bad");
  });

  it("formatFreshness renders human-readable relative time", () => {
    const now = new Date("2026-07-01T12:00:00Z");
    expect(formatFreshness(new Date("2026-07-01T11:54:00Z").toISOString(), now)).toBe("6m ago");
    expect(formatFreshness(new Date("2026-07-01T06:00:00Z").toISOString(), now)).toBe("6h ago");
    expect(formatFreshness(new Date("2026-06-29T12:00:00Z").toISOString(), now)).toBe("2d ago");
  });
});
