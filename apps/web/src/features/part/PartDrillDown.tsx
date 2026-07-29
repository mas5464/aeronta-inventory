import { useEffect, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DemandTrend } from "@/components/DemandTrend";
import { Metric } from "@/components/Metric";
import { ProvChip } from "@/components/ProvChip";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { CandidateComparisonPanel } from "@/features/part/CandidateComparisonPanel";
import { OpenRepairPipelinePanel } from "@/features/part/OpenRepairPipelinePanel";
import { PlanningTracePanel } from "@/features/part/PlanningTracePanel";
import { RepairReturnProfilePanel } from "@/features/part/RepairReturnProfilePanel";
import { RollbackConfirmDialog } from "@/features/part/RollbackConfirmDialog";
import { WritebackHistory } from "@/features/part/WritebackHistory";
import { rollbackResultMessage } from "@/features/part/writebackView";
import { usePartContext } from "@/lib/api/usePartContext";
import { useRollback } from "@/lib/api/useWriteback";
import type {
  HistoryEntry,
  LeadTimeView,
  RollbackRequest,
  SupplyCycleCondition,
  SupplyCycleLaneView,
} from "@/lib/api/types";
import {
  costProvenance,
  demandProvenance,
  openOrdersProvenance,
  policyProvenance,
  stockProvenance,
  supplyCycleProvenance,
} from "@/lib/partProvenance";
import { withProvenance } from "@/lib/provenance";

const integerFormatter = new Intl.NumberFormat("en-US");
const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

function formatPolicy(rop: number, eoq: number, ss: number, max: number): string {
  return `ROP ${integerFormatter.format(rop)} · EOQ ${integerFormatter.format(eoq)} · SS ${integerFormatter.format(
    ss,
  )} · Max ${integerFormatter.format(max)}`;
}

function formatDays(days: number | null): string {
  return days === null ? "—" : `${days.toFixed(1)}d`;
}

function CycleStatistic({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-2">{label}</dt>
      <dd className="mt-1 text-xl font-semibold text-ink">{value}</dd>
    </div>
  );
}

function EvidenceRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-2 text-xs">
      <dt className="text-ink-3">{label}</dt>
      <dd className="break-words text-ink-2">{value}</dd>
    </div>
  );
}

function SupplyCycleCard({
  title,
  condition,
  lane,
  legacyLeadTime,
}: {
  title: string;
  condition: SupplyCycleCondition;
  lane: SupplyCycleLaneView | undefined;
  legacyLeadTime?: LeadTimeView | null;
}) {
  const conditionMismatch = lane !== undefined && lane.condition !== condition;
  const modernLane = !conditionMismatch ? lane : undefined;
  const legacyNew =
    condition === "NEW" && lane === undefined ? legacyLeadTime : undefined;

  if (modernLane) {
    const evidence = supplyCycleProvenance(modernLane);
    const unavailable = modernLane.status === "unavailable";
    const fallback = modernLane.status === "configured_fallback";

    return (
      <Card
        role="region"
        aria-label={`${title} (${condition})`}
        data-testid={`supply-cycle-${condition.toLowerCase()}`}
      >
        <CardHeader className="gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>{title}</CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <Badge>{condition}</Badge>
              <Badge
                variant={evidence.statusVariant}
                aria-label={`${title} evidence status: ${evidence.statusLabel}`}
              >
                {evidence.statusLabel}
              </Badge>
            </div>
          </div>
          {evidence.proxyLabel && (
            <Badge
              variant="warn"
              className="w-fit"
              aria-label={`${title} proxy label: ${evidence.proxyLabel}`}
            >
              {evidence.proxyLabel}
            </Badge>
          )}
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {unavailable && (
            <p className="text-sm text-ink-2" role="note">
              {evidence.unavailableReason ??
                `${title} evidence is unavailable for this part.`}
            </p>
          )}
          {fallback && (
            <p className="text-sm text-ink-2" role="note">
              {condition === "REP"
                ? "Configured repair promise; no observed repair-cycle distribution is available."
                : "Configured procurement promise; no observed procurement distribution is available."}
            </p>
          )}
          {modernLane.status === "observed" && condition === "REP" && (
            <p className="text-xs text-ink-2" role="note">
              Creation-to-last-receipt is descriptive repair TAT, not projected
              repair supply.
            </p>
          )}

          <dl
            className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3"
            aria-label={`${title} distribution statistics`}
          >
            <CycleStatistic
              label="Mean"
              value={formatDays(unavailable ? null : modernLane.mean_days)}
            />
            <CycleStatistic
              label="P50"
              value={formatDays(unavailable ? null : modernLane.p50_days)}
            />
            <CycleStatistic
              label="P90"
              value={formatDays(unavailable ? null : modernLane.p90_days)}
            />
            <CycleStatistic
              label="P99"
              value={formatDays(unavailable ? null : modernLane.p99_days)}
            />
            <CycleStatistic
              label="Observations"
              value={
                unavailable
                  ? "—"
                  : integerFormatter.format(modernLane.n_observations)
              }
            />
          </dl>

          <dl
            className="flex flex-col gap-1.5 border-t border-line pt-3"
            aria-label={`${title} evidence provenance`}
          >
            <EvidenceRow label="Source" value={evidence.sourceLabel} />
            <EvidenceRow label="Grouping" value={evidence.groupingLabel} />
            <EvidenceRow
              label="Confidence"
              value={evidence.confidenceLabel}
            />
            <EvidenceRow
              label="Data cutoff"
              value={evidence.dataCutoffLabel}
            />
            <EvidenceRow
              label="Model version"
              value={evidence.modelVersionLabel}
            />
            <EvidenceRow
              label="Classification"
              value={evidence.classificationLabel}
            />
            <EvidenceRow
              label="Proxy definition"
              value={evidence.proxyDefinitionLabel}
            />
          </dl>
        </CardContent>
      </Card>
    );
  }

  const missingReason = conditionMismatch
    ? `The returned ${lane?.condition ?? "unknown"} lane does not match ${condition}; its evidence was withheld.`
    : `${title} is absent from this legacy response.`;

  return (
    <Card
      role="region"
      aria-label={`${title} (${condition})`}
      data-testid={`supply-cycle-${condition.toLowerCase()}`}
    >
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>{title}</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Badge>{condition}</Badge>
            <Badge
              variant={legacyNew ? "warn" : "bad"}
              aria-label={`${title} evidence status: ${
                legacyNew ? "Legacy compatibility" : "Unavailable"
              }`}
            >
              {legacyNew ? "Legacy compatibility" : "Unavailable"}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="text-sm text-ink-2" role="note">
          {legacyNew
            ? "Legacy NEW-only lead-time values are shown without modern distribution provenance."
            : missingReason}
        </p>
        {legacyNew?.promised_days !== null &&
          legacyNew?.promised_days !== undefined && (
            <p className="text-xs text-ink-2">
              Legacy promised lead: {formatDays(legacyNew.promised_days)}
            </p>
          )}
        <dl
          className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3"
          aria-label={`${title} distribution statistics`}
        >
          <CycleStatistic
            label="Mean"
            value={formatDays(legacyNew?.realized_mean_days ?? null)}
          />
          <CycleStatistic label="P50" value="—" />
          <CycleStatistic label="P90" value="—" />
          <CycleStatistic label="P99" value="—" />
          <CycleStatistic
            label="Observations"
            value={
              legacyNew
                ? integerFormatter.format(legacyNew.n_observations)
                : "—"
            }
          />
        </dl>
        <dl
          className="flex flex-col gap-1.5 border-t border-line pt-3"
          aria-label={`${title} evidence provenance`}
        >
          <EvidenceRow label="Source" value="Unavailable" />
          <EvidenceRow label="Grouping" value="Unavailable" />
          <EvidenceRow label="Confidence" value="Unknown" />
          <EvidenceRow label="Data cutoff" value="Unavailable" />
          <EvidenceRow label="Model version" value="Unavailable" />
          <EvidenceRow label="Classification" value="Unavailable" />
          <EvidenceRow
            label="Proxy definition"
            value={condition === "NEW" ? "Not applicable" : "Unavailable"}
          />
        </dl>
      </CardContent>
    </Card>
  );
}

/**
 * Slice S2 — Part Drill-Down (read path). Renders the BFF's PartContext
 * (GET /v1/tenants/{tenant}/parts/{pn}/{location}) via the S1 provenance
 * primitives: header, stat cards (each a Metric+ProvChip), demand trend,
 * and open orders. Every surfaced value carries provenance honestly mapped
 * to its underlying eMRO extract feed (src/lib/partProvenance.ts).
 */
export function PartDrillDown() {
  const params = useParams<{ pn: string; location: string }>();
  const pn = params.pn ?? "";
  const location = params.location ?? "";
  const routeLocation = useLocation();
  const recommendationId = new URLSearchParams(routeLocation.search).get(
    "recommendation_id",
  );

  const { data, isPending, isError, error, refetch, dataUpdatedAt } =
    usePartContext(pn, location, undefined, recommendationId);

  const location_hash = routeLocation.hash;
  const [rollbackEntry, setRollbackEntry] = useState<HistoryEntry | null>(null);
  const rollbackMutation = useRollback();

  // Deep-link: honor #history by scrolling the section into view once rendered.
  useEffect(() => {
    if (location_hash === "#history") {
      document.getElementById("history")?.scrollIntoView({ behavior: "smooth" });
    }
  }, [location_hash]);

  if (isPending) {
    return <QueryLoading label={`Loading part ${pn} / ${location}…`} />;
  }

  if (isError) {
    return (
      <QueryError label={`Failed to load part ${pn} / ${location}`} error={error} onRetry={() => refetch()} />
    );
  }

  const {
    attributes,
    stock,
    current_policy,
    proposed_policy,
    lead_time,
    procurement_lead_time,
    repair_cycle_time,
    open_orders,
    total_open_qty,
    open_orders_status,
    demand,
    unit_cost,
    planning_trace,
    candidate_frontier,
    repair_pipeline,
    repair_return_profile,
  } = data;

  // Real fetch time, not render-time "now" — see Overview.tsx for why.
  const asOf = new Date(dataUpdatedAt);
  const stockProv = stockProvenance(asOf);
  const policyProv = policyProvenance(asOf);
  const demandProv = demandProvenance(asOf);
  const openOrdersStatus =
    open_orders_status ??
    planning_trace?.open_receipts_status ??
    (open_orders.length > 0 ? "available" : "unavailable");
  const ordersProv = openOrdersProvenance(asOf, openOrdersStatus);
  const costProv = costProvenance(asOf);

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <header className="flex flex-col gap-2 border-b border-line pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold text-ink">{pn}</h1>
          <span className="text-ink-3">·</span>
          <span className="text-ink-2">{location}</span>
          {attributes.criticality_tier !== null && (
            <Badge variant="brand" data-testid="criticality-badge">
              Tier {attributes.criticality_tier}
            </Badge>
          )}
          {attributes.ata_chapter && <Badge>ATA {attributes.ata_chapter}</Badge>}
          {attributes.part_class && <Badge>{attributes.part_class}</Badge>}
          {attributes.hazardous_material && <Badge variant="warn">Hazmat</Badge>}
          {attributes.tool_control_item && <Badge variant="warn">Tool-controlled</Badge>}
        </div>
        <p className="text-sm text-ink-2">{attributes.description}</p>
      </header>

      {/* Stat cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Stock position</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {stock ? (
              <>
                <Metric
                  label="On-hand"
                  metric={withProvenance(stock.on_hand, stockProv)}
                  format={integerFormatter.format}
                />
                <div className="flex gap-4">
                  <Metric
                    label="Serviceable"
                    metric={withProvenance(stock.serviceable, stockProv)}
                    format={integerFormatter.format}
                  />
                  <Metric
                    label="In-repair"
                    metric={withProvenance(stock.in_repair, stockProv)}
                    format={integerFormatter.format}
                  />
                </div>
              </>
            ) : (
              <p className="text-sm text-ink-2">No stock position on record.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Policy — current vs proposed</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {current_policy ? (
              <Metric
                label="Current"
                metric={withProvenance(current_policy, policyProv)}
                format={(p) => formatPolicy(p.rop, p.eoq, p.safety_stock, p.max_stock)}
              />
            ) : (
              <p className="text-sm text-ink-2">No current policy on record.</p>
            )}
            {proposed_policy ? (
              <Metric
                label="Proposed"
                metric={withProvenance(proposed_policy, policyProv)}
                format={(p) => formatPolicy(p.rop, p.eoq, p.safety_stock, p.max_stock)}
              />
            ) : (
              <p className="text-sm text-ink-2">No proposed policy change.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Demanded units (trailing 24 months)</CardTitle>
          </CardHeader>
          <CardContent>
            {demand ? (
              <Metric
                metric={withProvenance(demand.total_24mo, demandProv)}
                format={integerFormatter.format}
              />
            ) : (
              <p className="text-sm text-ink-2">No demand data for this part.</p>
            )}
          </CardContent>
        </Card>

        <SupplyCycleCard
          title="Procurement lead time"
          condition="NEW"
          lane={procurement_lead_time}
          legacyLeadTime={lead_time}
        />

        <SupplyCycleCard
          title="Repair cycle time"
          condition="REP"
          lane={repair_cycle_time}
        />

        <Card>
          <CardHeader>
            <CardTitle>Open orders</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {openOrdersStatus === "unavailable" ? (
              <>
                <p className="text-sm text-ink-2">
                  Open-order evidence is unavailable; counts are not observed zeros.
                </p>
                <ProvChip provenance={ordersProv} />
              </>
            ) : (
              <>
                <div className="flex gap-4">
                  <Metric
                    label="Count"
                    metric={withProvenance(open_orders.length, ordersProv)}
                    format={integerFormatter.format}
                  />
                  <Metric
                    label="Qty open"
                    metric={withProvenance(total_open_qty, ordersProv)}
                    format={integerFormatter.format}
                  />
                </div>
                {openOrdersStatus === "partial" && (
                  <p className="text-xs text-ink-2">
                    Open-order coverage is partial; undated lines may not be assigned
                    to the planning horizon.
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Unit cost</CardTitle>
          </CardHeader>
          <CardContent>
            {unit_cost !== null ? (
              <Metric metric={withProvenance(unit_cost, costProv)} format={currencyFormatter.format} />
            ) : (
              <p className="text-sm text-ink-2">No vendor economics on record.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Demand drivers / trend */}
      <Card>
        <CardHeader>
          <CardTitle>Demand history</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <DemandTrend points={demand?.points ?? []} />
        </CardContent>
      </Card>

      {planning_trace && <PlanningTracePanel trace={planning_trace} asOf={asOf} />}

      <OpenRepairPipelinePanel pipeline={repair_pipeline} />

      <RepairReturnProfilePanel profile={repair_return_profile} />

      {candidate_frontier && (
        <CandidateComparisonPanel frontier={candidate_frontier} />
      )}

      {/* Open orders list */}
      <Card>
        <CardHeader>
          <CardTitle>Open orders detail</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-ink-2" role="note">
            PO lines are procurement receipts. RO lines are reconciled only in
            the open repair pipeline and are never counted as generic receipt
            credit.
          </p>
          {openOrdersStatus === "unavailable" ? (
            <p className="text-sm text-ink-2">
              Open-order detail is unavailable for this key.
            </p>
          ) : open_orders.length === 0 ? (
            <p className="text-sm text-ink-2">No open orders.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[66rem] text-left text-sm">
                <caption className="sr-only">Open orders for {pn} / {location}</caption>
                <thead>
                  <tr className="text-ink-2">
                    <th scope="col" className="pb-2 pr-4 font-medium">Order / line</th>
                    <th scope="col" className="pb-2 pr-4 font-medium">Type</th>
                    <th scope="col" className="pb-2 pr-4 font-medium">Status</th>
                    <th scope="col" className="pb-2 pr-4 font-medium">Vendor / shop</th>
                    <th scope="col" className="pb-2 pr-4 font-medium">Qty open</th>
                    <th scope="col" className="pb-2 pr-4 font-medium">Expected receipt</th>
                    <th scope="col" className="pb-2 pr-4 font-medium">Opened</th>
                    <th scope="col" className="pb-2 pr-4 font-medium">Serial</th>
                    <th scope="col" className="pb-2 font-medium">Location</th>
                  </tr>
                </thead>
                <tbody>
                  {open_orders.map((order, index) => (
                    <tr
                      key={`${order.order_id}:${order.order_line_id ?? index}`}
                      className="border-t border-line align-top"
                    >
                      <td className="py-2 pr-4">
                        <span className="block">{order.order_id}</span>
                        <span className="text-xs text-ink-3">
                          Line {order.order_line_id ?? "—"}
                        </span>
                      </td>
                      <td className="py-2 pr-4">{order.order_type}</td>
                      <td className="py-2 pr-4">{order.status ?? "—"}</td>
                      <td className="py-2 pr-4">
                        <span className="block">Vendor {order.vendor ?? "—"}</span>
                        <span className="text-xs text-ink-3">
                          Shop {order.shop ?? "—"}
                        </span>
                      </td>
                      <td className="py-2 pr-4">{integerFormatter.format(order.qty_open)}</td>
                      <td className="py-2 pr-4">{order.expected_rcv_date ?? "—"}</td>
                      <td className="py-2 pr-4">{order.opened_at ?? "—"}</td>
                      <td className="py-2 pr-4">{order.serial_number ?? "—"}</td>
                      <td className="py-2">{order.location ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <WritebackHistory
        pn={pn}
        location={location}
        onRollback={(entry) => {
          // Clear any prior attempt's result so a stale message can't bleed
          // into a freshly-opened dialog.
          rollbackMutation.reset();
          setRollbackEntry(entry);
        }}
      />

      {rollbackEntry && (
        <RollbackConfirmDialog
          entry={rollbackEntry}
          isSubmitting={rollbackMutation.isPending}
          resultError={rollbackMutation.data ? rollbackResultMessage(rollbackMutation.data) : null}
          onCancel={() => {
            rollbackMutation.reset();
            setRollbackEntry(null);
          }}
          onConfirm={(reason) => {
            const req: RollbackRequest = {
              tenant_id: "acme", pn, location, reason, principal: "planner",
              requested_at: new Date().toISOString(),
            };
            // Close only on a clean rollback; a non-rolled_back result
            // (outside_window / nothing_to_revert) keeps the dialog open and
            // surfaces the mapped message via `resultError` above.
            rollbackMutation.mutate(req, {
              onSuccess: (res) => {
                if (res.status === "rolled_back") setRollbackEntry(null);
              },
            });
          }}
        />
      )}
    </div>
  );
}
