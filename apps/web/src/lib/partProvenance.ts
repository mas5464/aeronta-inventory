import type {
  PlanningEvidenceAvailability,
  SupplyCycleLaneView,
} from "@/lib/api/types";
import type { Provenance } from "@/lib/provenance";

/**
 * Provenance stamps for `PartContext` fields (Slice S2 — Part Drill-Down).
 *
 * The BFF's `PartContext` (services/agent-spine/src/trax_io_spine/bff/store.py
 * `part_context()`) assembles the view from several Feature Store reads, each
 * backed by a distinct eMRO extract feed:
 *   - stock, current/proposed policy → INVENTORY (PN_INVENTORY_LEVEL + stock position)
 *   - lead time, open orders        → LEAD_TIME / PURCHASE_ORDERS feeds
 *   - demand history                → DEMAND_HISTORY (removals + issues)
 *   - unit cost                     → VENDOR (vendor economics)
 *
 * There is no per-field provenance on the wire yet, so — same honesty
 * standard as dashboardProvenance.ts — we stamp each surfaced metric with
 * the true origin of the underlying feed rather than inventing per-field
 * freshness/coverage/confidence the BFF doesn't actually report.
 */

function stamp(
  source: string,
  systemOfRecord: string,
  opts: { derived?: boolean; confidence?: number; asOf?: Date } = {},
): Provenance {
  const asOf = opts.asOf ?? new Date();
  return {
    source,
    systemOfRecord,
    freshnessAt: asOf.toISOString(),
    coverage: 1,
    confidence: opts.confidence ?? 0.95,
    derived: opts.derived ?? false,
  };
}

/** Stock breakdown (on-hand / serviceable / in-repair / allocated / rental / loan). */
export function stockProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Inventory", "INVENTORY", { asOf });
}

/** Current + proposed (ROP/EOQ/Safety Stock/Max) policy. */
export function policyProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Inventory", "INVENTORY", { derived: true, asOf });
}

/** Projected demand / demand history trend. */
export function demandProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Demand History", "DEMAND_HISTORY", { derived: true, confidence: 0.85, asOf });
}

/** Legacy lead time (promised vs realized); modern supply-cycle lanes use wire evidence. */
export function leadTimeProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Lead Time", "LEAD_TIME", { confidence: 0.9, asOf });
}

export interface SupplyCycleProvenanceView {
  statusLabel: "Observed" | "Configured fallback" | "Unavailable";
  statusVariant: "good" | "warn" | "bad";
  sourceLabel: string;
  groupingLabel: string;
  confidenceLabel: string;
  dataCutoffLabel: string;
  modelVersionLabel: string;
  classificationLabel: string;
  proxyDefinitionLabel: string;
  proxyLabel: SupplyCycleLaneView["proxy_label"];
  unavailableReason: string | null;
}

const SUPPLY_CYCLE_SOURCE_LABEL: Record<
  NonNullable<SupplyCycleLaneView["source"]>,
  string
> = {
  order_plan_closed_orders: "Closed orders · order_plan_closed_orders",
  pn_vendor_price: "Configured promise · pn_vendor_price",
};

const SUPPLY_CYCLE_GROUPING_LABEL: Record<
  NonNullable<SupplyCycleLaneView["grouping_level"]>,
  string
> = {
  part_vendor_condition: "Part + vendor + condition",
  part_condition: "Part + condition",
};

const SUPPLY_CYCLE_CLASSIFICATION_LABEL: Record<
  NonNullable<SupplyCycleLaneView["classification_source"]>,
  string
> = {
  explicit_order_type: "Explicit order type",
  legacy_order_id_prefix: "Legacy order-ID prefix",
  configured_condition: "Configured condition",
};

const SUPPLY_CYCLE_PROXY_LABEL: Record<
  NonNullable<SupplyCycleLaneView["proxy_definition"]>,
  string
> = {
  order_creation_to_last_receipt: "Order creation to last receipt",
  configured_repair_promise: "Configured repair promise",
};

/**
 * Planner-facing supply-cycle evidence copied only from the lane wire object.
 *
 * Unlike the legacy `Provenance` stamp, this does not convert categorical
 * confidence into an invented percentage and does not substitute fetch time
 * for the feature's own data cutoff.
 */
export function supplyCycleProvenance(
  lane: SupplyCycleLaneView,
): SupplyCycleProvenanceView {
  if (lane.status === "unavailable") {
    return {
      statusLabel: "Unavailable",
      statusVariant: "bad",
      sourceLabel: "Unavailable",
      groupingLabel: "Unavailable",
      confidenceLabel: "Unknown",
      dataCutoffLabel: "Unavailable",
      modelVersionLabel: "Unavailable",
      classificationLabel: "Unavailable",
      proxyDefinitionLabel:
        lane.condition === "NEW" ? "Not applicable" : "Unavailable",
      proxyLabel: null,
      unavailableReason: lane.unavailable_reason,
    };
  }

  const status =
    lane.status === "observed"
      ? { statusLabel: "Observed" as const, statusVariant: "good" as const }
      : {
          statusLabel: "Configured fallback" as const,
          statusVariant: "warn" as const,
        };
  const expectedRepairProxy =
    lane.condition === "REP" &&
    ((lane.status === "observed" &&
      lane.proxy_definition === "order_creation_to_last_receipt" &&
      lane.proxy_label === "RO cycle-time proxy") ||
      (lane.status === "configured_fallback" &&
        lane.proxy_definition === "configured_repair_promise" &&
        lane.proxy_label === "Configured repair promise"));

  return {
    ...status,
    sourceLabel: lane.source
      ? SUPPLY_CYCLE_SOURCE_LABEL[lane.source]
      : "Unavailable",
    groupingLabel: lane.grouping_level
      ? SUPPLY_CYCLE_GROUPING_LABEL[lane.grouping_level]
      : "Unavailable",
    confidenceLabel:
      lane.confidence === "unknown"
        ? "Unknown"
        : `${lane.confidence[0].toUpperCase()}${lane.confidence.slice(1)}`,
    dataCutoffLabel: lane.data_cutoff ?? "Unavailable",
    modelVersionLabel: lane.model_version ?? "Unavailable",
    classificationLabel: lane.classification_source
      ? SUPPLY_CYCLE_CLASSIFICATION_LABEL[lane.classification_source]
      : "Unavailable",
    proxyDefinitionLabel:
      lane.condition === "NEW"
        ? "Not applicable"
        : expectedRepairProxy && lane.proxy_definition
          ? SUPPLY_CYCLE_PROXY_LABEL[lane.proxy_definition]
          : "Unavailable",
    proxyLabel: expectedRepairProxy ? lane.proxy_label : null,
    unavailableReason: lane.unavailable_reason,
  };
}

/** Open orders count + qty, preserving observed-empty vs unavailable coverage. */
export function openOrdersProvenance(
  asOf: Date = new Date(),
  status: PlanningEvidenceAvailability = "available",
): Provenance {
  const provenance = stamp("eMRO Purchase Orders", "PURCHASE_ORDERS", { asOf });
  if (status === "available") return provenance;
  if (status === "partial") {
    return {
      ...provenance,
      coverage: 0.65,
      confidence: 0.65,
      derived: true,
    };
  }
  return {
    ...provenance,
    coverage: 0,
    confidence: 0,
    derived: true,
  };
}

/** Unit cost. */
export function costProvenance(asOf: Date = new Date()): Provenance {
  return stamp("Vendor Master", "VENDOR", { asOf });
}

/** Known scheduled demand included only inside the selected planning horizon. */
export function scheduledDemandProvenance(asOf: Date = new Date()): Provenance {
  return stamp("eMRO Scheduled Demand", "SCHEDULED_DEMAND", {
    derived: true,
    confidence: 0.85,
    asOf,
  });
}

/** Constraint evidence carries its concrete source on the planning-trace wire. */
export function planningConstraintProvenance(
  source: string,
  asOf: Date = new Date(),
): Provenance {
  const sourceLabel = source.trim() || "Planning constraint source unavailable";
  return stamp(sourceLabel, "PLANNING_CONSTRAINT", {
    derived: true,
    confidence: source.trim() ? 0.9 : 0.5,
    asOf,
  });
}
