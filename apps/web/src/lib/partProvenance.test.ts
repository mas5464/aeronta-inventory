import { describe, expect, it } from "vitest";
import type { SupplyCycleLaneView } from "@/lib/api/types";
import { supplyCycleProvenance } from "@/lib/partProvenance";

describe("supplyCycleProvenance", () => {
  it("preserves observed REP evidence and the exact proxy label", () => {
    const lane: SupplyCycleLaneView = {
      condition: "REP",
      status: "observed",
      mean_days: 61,
      p50_days: 58,
      p90_days: 80,
      p99_days: 95,
      n_observations: 9,
      source: "order_plan_closed_orders",
      grouping_level: "part_vendor_condition",
      confidence: "low",
      data_cutoff: "2026-07-26",
      model_version: "supply-cycle-v1",
      classification_source: "legacy_order_id_prefix",
      proxy_definition: "order_creation_to_last_receipt",
      proxy_label: "RO cycle-time proxy",
      unavailable_reason: null,
    };

    expect(supplyCycleProvenance(lane)).toEqual({
      statusLabel: "Observed",
      statusVariant: "good",
      sourceLabel: "Closed orders · order_plan_closed_orders",
      groupingLabel: "Part + vendor + condition",
      confidenceLabel: "Low",
      dataCutoffLabel: "2026-07-26",
      modelVersionLabel: "supply-cycle-v1",
      classificationLabel: "Legacy order-ID prefix",
      proxyDefinitionLabel: "Order creation to last receipt",
      proxyLabel: "RO cycle-time proxy",
      unavailableReason: null,
    });
  });

  it("does not invent provenance for an unavailable lane", () => {
    const lane: SupplyCycleLaneView = {
      condition: "NEW",
      status: "unavailable",
      mean_days: null,
      p50_days: null,
      p90_days: null,
      p99_days: null,
      n_observations: 0,
      source: null,
      grouping_level: null,
      confidence: "unknown",
      data_cutoff: null,
      model_version: null,
      classification_source: null,
      proxy_definition: null,
      proxy_label: null,
      unavailable_reason: "No NEW evidence.",
    };

    expect(supplyCycleProvenance(lane)).toEqual({
      statusLabel: "Unavailable",
      statusVariant: "bad",
      sourceLabel: "Unavailable",
      groupingLabel: "Unavailable",
      confidenceLabel: "Unknown",
      dataCutoffLabel: "Unavailable",
      modelVersionLabel: "Unavailable",
      classificationLabel: "Unavailable",
      proxyDefinitionLabel: "Not applicable",
      proxyLabel: null,
      unavailableReason: "No NEW evidence.",
    });
  });

  it("keeps configured repair promise distinct from observed RO proxy evidence", () => {
    const lane: SupplyCycleLaneView = {
      condition: "REP",
      status: "configured_fallback",
      mean_days: 70,
      p50_days: 70,
      p90_days: 70,
      p99_days: 70,
      n_observations: 0,
      source: "pn_vendor_price",
      grouping_level: "part_condition",
      confidence: "low",
      data_cutoff: "2026-07-25",
      model_version: "supply-cycle-v1",
      classification_source: "configured_condition",
      proxy_definition: "configured_repair_promise",
      proxy_label: "Configured repair promise",
      unavailable_reason: null,
    };

    const evidence = supplyCycleProvenance(lane);
    expect(evidence.statusLabel).toBe("Configured fallback");
    expect(evidence.proxyDefinitionLabel).toBe("Configured repair promise");
    expect(evidence.proxyLabel).toBe("Configured repair promise");
    expect(evidence.proxyLabel).not.toBe("RO cycle-time proxy");
  });
});
