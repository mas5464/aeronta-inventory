import { useEffect, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DemandTrend } from "@/components/DemandTrend";
import { Metric } from "@/components/Metric";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { RollbackConfirmDialog } from "@/features/part/RollbackConfirmDialog";
import { WritebackHistory } from "@/features/part/WritebackHistory";
import { usePartContext } from "@/lib/api/usePartContext";
import { useRollback } from "@/lib/api/useWriteback";
import type { HistoryEntry, RollbackRequest } from "@/lib/api/types";
import {
  costProvenance,
  demandProvenance,
  leadTimeProvenance,
  openOrdersProvenance,
  policyProvenance,
  stockProvenance,
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

  const { data, isPending, isError, error, refetch, dataUpdatedAt } = usePartContext(pn, location);

  const location_hash = useLocation().hash;
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

  const { attributes, stock, current_policy, proposed_policy, lead_time, open_orders, total_open_qty, demand, unit_cost } =
    data;

  // Real fetch time, not render-time "now" — see Overview.tsx for why.
  const asOf = new Date(dataUpdatedAt);
  const stockProv = stockProvenance(asOf);
  const policyProv = policyProvenance(asOf);
  const demandProv = demandProvenance(asOf);
  const leadProv = leadTimeProvenance(asOf);
  const ordersProv = openOrdersProvenance(asOf);
  const costProv = costProvenance(asOf);

  const need = demand ? Math.max(0, demand.total_24mo - (stock?.on_hand ?? 0)) : null;

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
            <CardTitle>Need / shortage</CardTitle>
          </CardHeader>
          <CardContent>
            {need !== null ? (
              <Metric metric={withProvenance(need, demandProv)} format={integerFormatter.format} />
            ) : (
              <p className="text-sm text-ink-2">Insufficient data.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Projected demand (24mo)</CardTitle>
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

        <Card>
          <CardHeader>
            <CardTitle>Lead time</CardTitle>
          </CardHeader>
          <CardContent className="flex gap-4">
            {lead_time ? (
              <>
                <Metric
                  label="Promised"
                  metric={withProvenance(lead_time.promised_days, leadProv)}
                  format={formatDays}
                />
                <Metric
                  label="Realized (mean)"
                  metric={withProvenance(lead_time.realized_mean_days, leadProv)}
                  format={formatDays}
                />
              </>
            ) : (
              <p className="text-sm text-ink-2">No lead time data.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Open orders</CardTitle>
          </CardHeader>
          <CardContent className="flex gap-4">
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

      {/* Open orders list */}
      <Card>
        <CardHeader>
          <CardTitle>Open orders detail</CardTitle>
        </CardHeader>
        <CardContent>
          {open_orders.length === 0 ? (
            <p className="text-sm text-ink-2">No open orders.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Open orders for {pn} / {location}</caption>
              <thead>
                <tr className="text-ink-2">
                  <th scope="col" className="pb-2 pr-4 font-medium">Order</th>
                  <th scope="col" className="pb-2 pr-4 font-medium">Type</th>
                  <th scope="col" className="pb-2 pr-4 font-medium">Vendor</th>
                  <th scope="col" className="pb-2 pr-4 font-medium">Qty open</th>
                  <th scope="col" className="pb-2 font-medium">Expected receipt</th>
                </tr>
              </thead>
              <tbody>
                {open_orders.map((order) => (
                  <tr key={order.order_id} className="border-t border-line">
                    <td className="py-2 pr-4">{order.order_id}</td>
                    <td className="py-2 pr-4">{order.order_type}</td>
                    <td className="py-2 pr-4">{order.vendor ?? "—"}</td>
                    <td className="py-2 pr-4">{integerFormatter.format(order.qty_open)}</td>
                    <td className="py-2">{order.expected_rcv_date ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      <WritebackHistory pn={pn} location={location} onRollback={setRollbackEntry} />

      {rollbackEntry && (
        <RollbackConfirmDialog
          entry={rollbackEntry}
          isSubmitting={rollbackMutation.isPending}
          resultError={rollbackMutation.data?.error_message ?? null}
          onCancel={() => setRollbackEntry(null)}
          onConfirm={(reason) => {
            const req: RollbackRequest = {
              tenant_id: "acme", pn, location, reason, principal: "planner",
              requested_at: new Date().toISOString(),
            };
            rollbackMutation.mutate(req, { onSuccess: (res) => { if (res.status === "rolled_back") setRollbackEntry(null); } });
          }}
        />
      )}
    </div>
  );
}
