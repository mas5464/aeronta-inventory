import { useId } from "react";
import { Metric } from "@/components/Metric";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  PlanningCalculationSource,
  PlanningEvidenceAvailability,
  PlanningEventCountSource,
  PlanningMemberTrace,
  PlanningTrace,
} from "@/lib/api/types";
import {
  demandProvenance,
  openOrdersProvenance,
  planningConstraintProvenance,
  policyProvenance,
  scheduledDemandProvenance,
} from "@/lib/partProvenance";
import type { Provenance } from "@/lib/provenance";
import { withProvenance } from "@/lib/provenance";

const integerFormatter = new Intl.NumberFormat("en-US");
const quantityFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});
const equationQuantityFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 6,
});
const rateFormatter = new Intl.NumberFormat("en-US", {
  maximumSignificantDigits: 3,
});
const dateFormatter = new Intl.DateTimeFormat("en-US", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

const EVENT_SOURCE_LABEL: Record<PlanningEventCountSource, string> = {
  observed: "Observed source events",
  bucket_fallback: "Bucket fallback estimate",
  unavailable: "Event count unavailable",
};

const CALCULATION_SOURCE_LABEL: Record<PlanningCalculationSource, string> = {
  served_calculation: "Exact served calculation",
  legacy_reconstructed: "Legacy reconstructed",
  unavailable: "Served calculation unavailable",
};

const AVAILABILITY_LABEL: Record<PlanningEvidenceAvailability, string> = {
  available: "Available",
  partial: "Partial",
  unavailable: "Unavailable",
};

const POOLING_SCOPE_LABEL = {
  single_key: "Single key",
  complete_group: "Complete interchange group",
  worklist_partial: "Partial worklist pool",
} as const;

interface ExactCalculation {
  projectionKind: string;
  servedHistoricalPerDay: number;
  projectedHistoricalDemand: number;
  scheduledDemandStatus: PlanningEvidenceAvailability;
  scheduledDemandUndatedLines: number;
  scheduledDemandUndatedUnits: number;
  scheduledDemandDue: number;
  projectedDemand: number;
  dispatchableAvailable: number;
  openReceiptsStatus: PlanningEvidenceAvailability;
  openReceiptsUndatedLines: number;
  openReceiptsUndatedUnits: number;
  openReceiptsDue: number;
  overdueOpenReceiptsDue: number;
  repairReceiptsDue: number;
  expectedReceiptsDue: number;
  netPosition: number;
  shortageBeforeAction: number;
  pooledGroupId: string | null;
  poolingScope: "single_key" | "complete_group" | "worklist_partial";
  excludedMemberKeys: string[];
  members: PlanningMemberTrace[];
}

function exactCalculation(trace: PlanningTrace): ExactCalculation | null {
  if (
    trace.calculation_source !== "served_calculation" ||
    !trace.projection_kind ||
    trace.served_historical_per_day === null ||
    trace.served_historical_per_day === undefined ||
    trace.projected_demand === null ||
    trace.projected_demand === undefined ||
    trace.dispatchable_available === null ||
    trace.dispatchable_available === undefined ||
    trace.repair_receipts_due === null ||
    trace.repair_receipts_due === undefined ||
    trace.expected_receipts_due === null ||
    trace.expected_receipts_due === undefined ||
    trace.net_position === null ||
    trace.net_position === undefined ||
    trace.shortage_before_action === null ||
    trace.shortage_before_action === undefined
  ) {
    return null;
  }

  return {
    projectionKind: trace.projection_kind,
    servedHistoricalPerDay: trace.served_historical_per_day,
    projectedHistoricalDemand: trace.projected_historical_demand,
    scheduledDemandStatus: trace.scheduled_demand_status ?? "unavailable",
    scheduledDemandUndatedLines: trace.scheduled_demand_undated_lines ?? 0,
    scheduledDemandUndatedUnits: trace.scheduled_demand_undated_units ?? 0,
    scheduledDemandDue: trace.scheduled_demand_due,
    projectedDemand: trace.projected_demand,
    dispatchableAvailable: trace.dispatchable_available,
    openReceiptsStatus: trace.open_receipts_status ?? "unavailable",
    openReceiptsUndatedLines: trace.open_receipts_undated_lines ?? 0,
    openReceiptsUndatedUnits: trace.open_receipts_undated_units ?? 0,
    openReceiptsDue: trace.open_receipts_due,
    overdueOpenReceiptsDue: trace.overdue_open_receipts_due ?? 0,
    repairReceiptsDue: trace.repair_receipts_due,
    expectedReceiptsDue: trace.expected_receipts_due,
    netPosition: trace.net_position,
    shortageBeforeAction: trace.shortage_before_action,
    pooledGroupId: trace.pooled_group_id ?? null,
    poolingScope: trace.pooling_scope ?? "single_key",
    excludedMemberKeys: trace.excluded_member_keys ?? [],
    members: trace.members ?? [],
  };
}

function formatDateOnly(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match) {
    const year = Number(match[1]);
    const monthIndex = Number(match[2]) - 1;
    const day = Number(match[3]);
    const date = new Date(Date.UTC(year, monthIndex, day));
    if (
      date.getUTCFullYear() === year &&
      date.getUTCMonth() === monthIndex &&
      date.getUTCDate() === day
    ) {
      return dateFormatter.format(date);
    }
    return value;
  }

  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date);
}

function formatClosedWindow(start: string | null | undefined, end: string | null | undefined): string {
  if (start && end) {
    return `${formatDateOnly(start)} – ${formatDateOnly(end)} (inclusive)`;
  }
  if (start) return `${formatDateOnly(start)} – end unavailable`;
  if (end) return `Start unavailable – ${formatDateOnly(end)}`;
  return "Unavailable";
}

function formatIntegerUnit(value: number, singular: string, plural = `${singular}s`): string {
  return `${integerFormatter.format(value)} ${value === 1 ? singular : plural}`;
}

function formatQuantity(value: number): string {
  return `${quantityFormatter.format(value)} units`;
}

function formatEquationQuantity(value: number): string {
  return `${equationQuantityFormatter.format(value)} units`;
}

function EvidenceAvailabilityBadge({
  label,
  status,
}: {
  label: string;
  status: PlanningEvidenceAvailability;
}) {
  return (
    <Badge
      variant={
        status === "available" ? "good" : status === "partial" ? "warn" : "bad"
      }
      className="w-fit"
      aria-label={`${label} evidence status: ${AVAILABILITY_LABEL[status]}`}
    >
      {AVAILABILITY_LABEL[status]}
    </Badge>
  );
}

function availabilityProvenance(
  base: Provenance,
  status: PlanningEvidenceAvailability,
): Provenance {
  if (status === "available") return base;
  if (status === "partial") {
    return {
      ...base,
      coverage: Math.min(base.coverage, 0.65),
      confidence: Math.min(base.confidence, 0.65),
      derived: true,
    };
  }
  return {
    ...base,
    coverage: 0,
    confidence: 0,
    derived: true,
  };
}

function eventCountProvenance(
  base: Provenance,
  source: PlanningEventCountSource,
): Provenance {
  if (source === "observed") return base;
  if (source === "bucket_fallback") {
    return {
      ...base,
      confidence: Math.min(base.confidence, 0.65),
      derived: true,
    };
  }
  return {
    ...base,
    coverage: 0,
    confidence: 0,
    derived: true,
  };
}

export interface PlanningTracePanelProps {
  trace: PlanningTrace;
  /** Response fetch time used for the existing provenance freshness convention. */
  asOf: Date;
}

/**
 * Read-only evidence for the BFF's planning calculation. Values are displayed,
 * never recomputed: this component preserves the server's inclusive windows,
 * event-versus-unit distinction, due supply caveat, and constraint evidence.
 */
export function PlanningTracePanel({ trace, asOf }: PlanningTracePanelProps) {
  const headingId = useId();
  const observationHeadingId = useId();
  const horizonHeadingId = useId();
  const membersHeadingId = useId();
  const constraintsHeadingId = useId();
  const warningsHeadingId = useId();

  const calculationSource = trace.calculation_source ?? "unavailable";
  const exact = exactCalculation(trace);
  const demandAvailable =
    trace.observation_start !== null &&
    trace.observation_end !== null &&
    trace.bucket !== null;
  const demandProv = availabilityProvenance(
    demandProvenance(asOf),
    demandAvailable ? "available" : "unavailable",
  );
  const horizonProv = policyProvenance(asOf);
  const scheduledStatus = trace.scheduled_demand_status ?? "unavailable";
  const openReceiptsStatus = trace.open_receipts_status ?? "unavailable";
  const scheduledProv = availabilityProvenance(
    scheduledDemandProvenance(asOf),
    scheduledStatus,
  );
  const receiptsProv = availabilityProvenance(
    openOrdersProvenance(asOf),
    openReceiptsStatus,
  );
  const countProv = eventCountProvenance(demandProv, trace.event_count_source);
  const hasHorizonDates = trace.as_of !== undefined || trace.horizon_end !== undefined;
  const hasOverdueReceiptEvidence = trace.overdue_open_receipts_due !== undefined;
  const overdueReceiptUnits = trace.overdue_open_receipts_due ?? 0;

  return (
    <Card role="region" aria-labelledby={headingId}>
      <CardHeader className="border-b border-line">
        <CardTitle id={headingId} className="text-base text-ink">
          Planning calculation trace
        </CardTitle>
        <p className="max-w-3xl text-sm leading-6 text-ink-2">
          Read-only evidence used for this planning horizon. Historical events count
          business events; demanded units sum their quantities.
        </p>
        <Badge
          variant={
            calculationSource === "served_calculation"
              ? "good"
              : calculationSource === "legacy_reconstructed"
                ? "warn"
                : "bad"
          }
          className="mt-2 w-fit"
          aria-label={`Calculation evidence source: ${CALCULATION_SOURCE_LABEL[calculationSource]}`}
        >
          {CALCULATION_SOURCE_LABEL[calculationSource]}
        </Badge>
      </CardHeader>

      <CardContent className="flex flex-col gap-6 pt-4">
        <section aria-labelledby={observationHeadingId}>
          <h4 id={observationHeadingId} className="mb-3 text-sm font-semibold text-ink">
            Observation and exposure
          </h4>
          <p className="mb-4 text-sm text-ink-2">
            The historical window is a closed interval. Periods without an observed
            event remain explicit zero-filled periods.
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Metric
              label="Historical observation window"
              metric={withProvenance(
                formatClosedWindow(trace.observation_start, trace.observation_end),
                demandProv,
              )}
            />
            <Metric
              label="Historical exposure"
              metric={withProvenance(trace.exposure_days, demandProv)}
              format={(value) => formatIntegerUnit(value, "day")}
            />
            <Metric
              label="Demand bucket"
              metric={withProvenance(trace.bucket ?? "Unavailable", demandProv)}
              format={(value) =>
                value === "Unavailable"
                  ? value
                  : value.charAt(0).toUpperCase() + value.slice(1)
              }
            />
            <Metric
              label="Observed periods"
              metric={withProvenance(trace.observed_periods, demandProv)}
              format={(value) => formatIntegerUnit(value, "period")}
            />
            <Metric
              label="Zero-filled periods"
              metric={withProvenance(trace.zero_filled_periods, demandProv)}
              format={(value) => formatIntegerUnit(value, "period")}
            />
            <div className="flex flex-col gap-2">
              <Metric
                label="Demand events"
                metric={withProvenance(trace.demand_event_count, countProv)}
                format={(value) =>
                  value === null ? "Unavailable" : formatIntegerUnit(value, "event")
                }
              />
              <Badge
                variant={
                  trace.event_count_source === "observed"
                    ? "good"
                    : trace.event_count_source === "bucket_fallback"
                      ? "warn"
                      : "bad"
                }
                className="w-fit"
                aria-label={`Demand event count source: ${EVENT_SOURCE_LABEL[trace.event_count_source]}`}
              >
                {EVENT_SOURCE_LABEL[trace.event_count_source]}
              </Badge>
            </div>
            <Metric
              label="Demanded units"
              metric={withProvenance(trace.demanded_units, demandProv)}
              format={(value) => formatIntegerUnit(value, "unit")}
            />
            <Metric
              label="Raw observed historical demand rate"
              metric={withProvenance(trace.historical_per_day, demandProv)}
              format={(value) => `${rateFormatter.format(value)} units/day`}
            />
          </div>
        </section>

        <section aria-labelledby={horizonHeadingId} className="border-t border-line pt-5">
          <h4 id={horizonHeadingId} className="mb-3 text-sm font-semibold text-ink">
            Horizon demand and supply
          </h4>
          <p className="mb-4 text-sm text-ink-2">
            Scheduled demand uses due dates from the plan as-of date through the
            horizon end, inclusive. Still-open receipts expected by the horizon end
            are evidence of possible supply, not guaranteed arrivals.
          </p>
          {exact ? (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {hasHorizonDates && (
                  <Metric
                    label="Planning horizon dates"
                    metric={withProvenance(
                      formatClosedWindow(trace.as_of, trace.horizon_end),
                      horizonProv,
                    )}
                  />
                )}
                <Metric
                  label="Planning horizon length"
                  metric={withProvenance(trace.horizon_days, horizonProv)}
                  format={(value) => formatIntegerUnit(value, "day")}
                />
                <Metric
                  label="Served distribution"
                  metric={withProvenance(exact.projectionKind, horizonProv)}
                />
                <Metric
                  label="Served historical forecast rate"
                  metric={withProvenance(exact.servedHistoricalPerDay, demandProv)}
                  format={(value) => `${rateFormatter.format(value)} units/day`}
                />
                <Metric
                  label={`Served historical demand over ${integerFormatter.format(trace.horizon_days)} days`}
                  metric={withProvenance(
                    exact.projectedHistoricalDemand,
                    demandProv,
                  )}
                  format={formatQuantity}
                />
                <div className="flex flex-col gap-2">
                  <Metric
                    label="Scheduled demand due in horizon"
                    metric={withProvenance(
                      exact.scheduledDemandDue,
                      scheduledProv,
                    )}
                    format={formatQuantity}
                  />
                  <EvidenceAvailabilityBadge
                    label="Scheduled demand"
                    status={exact.scheduledDemandStatus}
                  />
                  {(exact.scheduledDemandUndatedLines > 0 ||
                    exact.scheduledDemandUndatedUnits > 0) && (
                    <p
                      className="text-xs text-ink-2"
                      aria-label="Undated scheduled demand excluded"
                    >
                      {formatIntegerUnit(
                        exact.scheduledDemandUndatedLines,
                        "undated line",
                      )}{" "}
                      · {formatQuantity(exact.scheduledDemandUndatedUnits)} excluded
                    </p>
                  )}
                </div>
                <Metric
                  label="Total projected demand"
                  metric={withProvenance(exact.projectedDemand, horizonProv)}
                  format={formatQuantity}
                />
                <Metric
                  label="Dispatchable available"
                  metric={withProvenance(exact.dispatchableAvailable, horizonProv)}
                  format={formatQuantity}
                />
                <div className="flex flex-col gap-2">
                  <Metric
                    label="Open receipts due by horizon (not guaranteed)"
                    metric={withProvenance(exact.openReceiptsDue, receiptsProv)}
                    format={formatQuantity}
                  />
                  <EvidenceAvailabilityBadge
                    label="Open receipts"
                    status={exact.openReceiptsStatus}
                  />
                  {(exact.openReceiptsUndatedLines > 0 ||
                    exact.openReceiptsUndatedUnits > 0) && (
                    <p
                      className="text-xs text-ink-2"
                      aria-label="Undated open receipts excluded"
                    >
                      {formatIntegerUnit(
                        exact.openReceiptsUndatedLines,
                        "undated line",
                      )}{" "}
                      · {formatQuantity(exact.openReceiptsUndatedUnits)} excluded
                    </p>
                  )}
                </div>
                <Metric
                  label="Overdue open receipt units included"
                  metric={withProvenance(
                    exact.overdueOpenReceiptsDue,
                    receiptsProv,
                  )}
                  format={(value) =>
                    `${quantityFormatter.format(value)} overdue open receipt ${
                      value === 1 ? "unit" : "units"
                    }`
                  }
                />
                <Metric
                  label="Repair receipts due"
                  metric={withProvenance(exact.repairReceiptsDue, horizonProv)}
                  format={formatQuantity}
                />
                <Metric
                  label="Expected receipts due"
                  metric={withProvenance(exact.expectedReceiptsDue, horizonProv)}
                  format={formatQuantity}
                />
                <Metric
                  label="Net position before action"
                  metric={withProvenance(exact.netPosition, horizonProv)}
                  format={formatQuantity}
                />
                <Metric
                  label="Shortage before action"
                  metric={withProvenance(exact.shortageBeforeAction, horizonProv)}
                  format={formatQuantity}
                />
              </div>

              <div
                role="group"
                aria-label="Exact served calculation reconciliation"
                className="mt-4 space-y-2 rounded-card border border-line bg-panel-2 p-4 text-sm text-ink"
              >
                <p className="font-medium">Exact persisted reconciliation</p>
                <p aria-label="Exact projected-demand reconciliation">
                  {formatEquationQuantity(exact.projectedHistoricalDemand)} projected
                  historical +{" "}
                  {formatEquationQuantity(exact.scheduledDemandDue)} scheduled ={" "}
                  {formatEquationQuantity(exact.projectedDemand)} projected demand
                </p>
                <p aria-label="Exact expected-receipts reconciliation">
                  {formatEquationQuantity(exact.openReceiptsDue)} open receipts +{" "}
                  {formatEquationQuantity(exact.repairReceiptsDue)} repair receipts ={" "}
                  {formatEquationQuantity(exact.expectedReceiptsDue)} expected receipts
                </p>
                <p aria-label="Exact net-position reconciliation">
                  {formatEquationQuantity(exact.dispatchableAvailable)} dispatchable +{" "}
                  {formatEquationQuantity(exact.expectedReceiptsDue)} expected receipts
                  − {formatEquationQuantity(exact.projectedDemand)} projected demand ={" "}
                  {formatEquationQuantity(exact.netPosition)} net position
                </p>
                <p className="text-ink-2">
                  Each operand and result above is supplied by the BFF from the
                  recommendation&apos;s persisted calculation evidence; the browser
                  does not calculate the result.
                </p>
              </div>

              {exact.repairReceiptsDue === 0 && (
                <div
                  role="note"
                  aria-label="Repair receipt methodology"
                  className="mt-4 rounded-card border border-line bg-panel-2 p-3 text-sm text-ink-2"
                >
                  Conservative Phase 1 rule: aggregate in-repair stock receives no
                  repair-return credit until identity-aware, age-conditioned return
                  evidence exists. A zero repair-receipt value is deliberate and is not
                  an observed promise.
                </div>
              )}
            </>
          ) : calculationSource === "legacy_reconstructed" ? (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {hasHorizonDates && (
                  <Metric
                    label="Planning horizon dates"
                    metric={withProvenance(
                      formatClosedWindow(trace.as_of, trace.horizon_end),
                      horizonProv,
                    )}
                  />
                )}
                <Metric
                  label="Planning horizon length"
                  metric={withProvenance(trace.horizon_days, horizonProv)}
                  format={(value) => formatIntegerUnit(value, "day")}
                />
                <Metric
                  label={`Legacy historical reconstruction over ${integerFormatter.format(trace.horizon_days)} days`}
                  metric={withProvenance(
                    trace.projected_historical_demand,
                    demandProv,
                  )}
                  format={formatQuantity}
                />
                <div className="flex flex-col gap-2">
                  <Metric
                    label="Legacy scheduled-demand reconstruction"
                    metric={withProvenance(
                      trace.scheduled_demand_due,
                      scheduledProv,
                    )}
                    format={formatQuantity}
                  />
                  <EvidenceAvailabilityBadge
                    label="Scheduled demand"
                    status={trace.scheduled_demand_status ?? "unavailable"}
                  />
                </div>
                {trace.projected_demand !== null &&
                  trace.projected_demand !== undefined && (
                    <Metric
                      label="Legacy recommendation projected demand"
                      metric={withProvenance(trace.projected_demand, horizonProv)}
                      format={formatQuantity}
                    />
                  )}
                <div className="flex flex-col gap-2">
                  <Metric
                    label="Legacy open-receipt reconstruction (not guaranteed)"
                    metric={withProvenance(trace.open_receipts_due, receiptsProv)}
                    format={formatQuantity}
                  />
                  <EvidenceAvailabilityBadge
                    label="Open receipts"
                    status={trace.open_receipts_status ?? "unavailable"}
                  />
                </div>
                {hasOverdueReceiptEvidence && (
                  <Metric
                    label="Overdue open receipt units included"
                    metric={withProvenance(overdueReceiptUnits, receiptsProv)}
                    format={(value) =>
                      `${quantityFormatter.format(value)} overdue open receipt ${
                        value === 1 ? "unit" : "units"
                      }`
                    }
                  />
                )}
              </div>
              <p
                role="note"
                aria-label="Legacy calculation limitation"
                className="mt-4 rounded-card border border-warn/40 bg-warn/10 p-3 text-sm text-ink"
              >
                Exact statistical, pooled, repair-receipt, and net-position
                reconciliation is unavailable for this legacy recommendation.
              </p>
            </>
          ) : (
            <p className="text-sm text-ink-2">
              No served recommendation calculation is available for this key.
            </p>
          )}
          {hasOverdueReceiptEvidence && overdueReceiptUnits > 0 && (
            <div
              role="note"
              aria-label="Overdue receipt reliability warning"
              className="mt-4 rounded-card border border-warn/40 bg-warn/10 p-3 text-sm text-ink"
            >
              Some still-open receipt units are overdue. They remain uncertain supply
              and are not guaranteed to arrive.
            </div>
          )}
        </section>

        {exact &&
          (exact.pooledGroupId ||
            exact.members.length > 1 ||
            exact.poolingScope !== "single_key") && (
          <section
            aria-labelledby={membersHeadingId}
            className="border-t border-line pt-5"
          >
            <h4 id={membersHeadingId} className="mb-2 text-sm font-semibold text-ink">
              Pooled member contributions
            </h4>
            <p className="mb-4 text-sm text-ink-2">
              Interchange group{" "}
              <span className="font-medium text-ink">
                {exact.pooledGroupId ?? "identifier unavailable"}
              </span>
              . The exact group totals are shown above; these are the persisted
              member operands and results.
            </p>
            <Badge
              variant={exact.poolingScope === "worklist_partial" ? "warn" : "good"}
              className="mb-3 w-fit"
              aria-label={`Pooling scope: ${POOLING_SCOPE_LABEL[exact.poolingScope]}`}
            >
              {POOLING_SCOPE_LABEL[exact.poolingScope]}
            </Badge>
            {exact.poolingScope === "worklist_partial" && (
              <p
                role="note"
                aria-label="Excluded interchange members"
                className="mb-4 rounded-card border border-warn/40 bg-warn/10 p-3 text-sm text-ink"
              >
                Not evaluated in this worklist:{" "}
                {exact.excludedMemberKeys.join(", ")}
              </p>
            )}
            <div className="overflow-x-auto rounded-card border border-line">
              <table className="min-w-full divide-y divide-line text-left text-sm">
                <caption className="sr-only">
                  Pooled member calculation contributions
                </caption>
                <thead className="bg-panel-2 text-xs uppercase tracking-wide text-ink-2">
                  <tr>
                    <th scope="col" className="px-3 py-2">Member</th>
                    <th scope="col" className="px-3 py-2">Distribution</th>
                    <th scope="col" className="px-3 py-2">Historical</th>
                    <th scope="col" className="px-3 py-2">Scheduled</th>
                    <th scope="col" className="px-3 py-2">Projected</th>
                    <th scope="col" className="px-3 py-2">Dispatchable</th>
                    <th scope="col" className="px-3 py-2">Open receipts</th>
                    <th scope="col" className="px-3 py-2">Overdue</th>
                    <th scope="col" className="px-3 py-2">Repair receipts</th>
                    <th scope="col" className="px-3 py-2">Expected receipts</th>
                    <th scope="col" className="px-3 py-2">Net position</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {exact.members.map((member) => (
                    <tr key={`${member.pn}-${member.location}`}>
                      <th scope="row" className="whitespace-nowrap px-3 py-2 font-medium text-ink">
                        {member.pn} · {member.location}
                      </th>
                      <td className="whitespace-nowrap px-3 py-2">{member.projection_kind}</td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {formatQuantity(member.projected_historical_demand)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <div>{formatQuantity(member.scheduled_demand_due)}</div>
                        <span
                          className="text-xs text-ink-2"
                          aria-label={`${member.pn} scheduled demand evidence status: ${
                            AVAILABILITY_LABEL[
                              member.scheduled_demand_status ?? "unavailable"
                            ]
                          }`}
                        >
                          {AVAILABILITY_LABEL[
                            member.scheduled_demand_status ?? "unavailable"
                          ]}
                        </span>
                        {((member.scheduled_demand_undated_lines ?? 0) > 0 ||
                          (member.scheduled_demand_undated_units ?? 0) > 0) && (
                          <div className="text-xs text-ink-2">
                            {member.scheduled_demand_undated_lines ?? 0} undated ·{" "}
                            {formatQuantity(
                              member.scheduled_demand_undated_units ?? 0,
                            )}
                          </div>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {formatQuantity(member.projected_demand)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {formatQuantity(member.dispatchable_available)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <div>{formatQuantity(member.open_receipts_due)}</div>
                        <span
                          className="text-xs text-ink-2"
                          aria-label={`${member.pn} open receipts evidence status: ${
                            AVAILABILITY_LABEL[
                              member.open_receipts_status ?? "unavailable"
                            ]
                          }`}
                        >
                          {AVAILABILITY_LABEL[
                            member.open_receipts_status ?? "unavailable"
                          ]}
                        </span>
                        {((member.open_receipts_undated_lines ?? 0) > 0 ||
                          (member.open_receipts_undated_units ?? 0) > 0) && (
                          <div className="text-xs text-ink-2">
                            {member.open_receipts_undated_lines ?? 0} undated ·{" "}
                            {formatQuantity(
                              member.open_receipts_undated_units ?? 0,
                            )}
                          </div>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {formatQuantity(member.overdue_open_receipts_due)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {formatQuantity(member.repair_receipts_due)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {formatQuantity(member.expected_receipts_due)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {formatQuantity(member.net_position)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <section aria-labelledby={constraintsHeadingId} className="border-t border-line pt-5">
          <h4 id={constraintsHeadingId} className="mb-3 text-sm font-semibold text-ink">
            Applied and binding constraints
          </h4>
          {trace.constraints.length === 0 ? (
            <p className="text-sm text-ink-2">No constraint evidence was reported.</p>
          ) : (
            <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {trace.constraints.map((constraint, index) => (
                <li
                  key={`${constraint.name}-${constraint.source}-${index}`}
                  className="rounded-card border border-line bg-panel-2 p-3"
                >
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <h5 className="font-medium text-ink">{constraint.name}</h5>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge
                        variant={constraint.scope === "action" ? "warn" : "default"}
                        aria-label={`${constraint.name} scope: ${
                          constraint.scope === "action" ? "Action" : "Policy"
                        }`}
                      >
                        {constraint.scope === "action" ? "Action" : "Policy"}
                      </Badge>
                      <Badge
                        variant={constraint.binding ? "warn" : "default"}
                        aria-label={`${constraint.name}: ${
                          constraint.binding ? "binding constraint" : "applied, not binding"
                        }`}
                      >
                        {constraint.binding ? "Binding" : "Applied · not binding"}
                      </Badge>
                    </div>
                  </div>
                  <Metric
                    label="Constraint value"
                    metric={withProvenance(
                      constraint.value ?? "Unavailable",
                      planningConstraintProvenance(constraint.source, asOf),
                    )}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>

        {trace.warnings.length > 0 && (
          <section
            role="note"
            aria-labelledby={warningsHeadingId}
            className="border-t border-line pt-5"
          >
            <h4 id={warningsHeadingId} className="mb-2 text-sm font-semibold text-warn">
              Planning warnings
            </h4>
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
              {trace.warnings.map((warning, index) => (
                <li key={`${warning}-${index}`}>{warning}</li>
              ))}
            </ul>
          </section>
        )}
      </CardContent>
    </Card>
  );
}
