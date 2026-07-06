import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { useBvr } from "@/lib/api/useBvr";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import { formatAmount, formatRatePct, savingsComponentLabel } from "@/features/reports/reportView";
import type { BvrSavings } from "@/lib/api/types";

const SAVINGS_KEYS: (keyof Pick<BvrSavings, "holding_cost_delta" | "ordering_cost_delta" | "stockout_risk_delta">)[] = [
  "holding_cost_delta",
  "ordering_cost_delta",
  "stockout_risk_delta",
];

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-control border border-line p-3">
      <div className="text-lg font-semibold text-ink">{value}</div>
      <div className="text-xs text-ink-2">{label}</div>
    </div>
  );
}

/**
 * The #8 Business Value Report rendered as a document — projected savings
 * attribution, governance, and forward-look, all against the pre-agent
 * baseline (BFF: `GET /v1/tenants/{tenant}/reports/bvr`).
 *
 * Deliberately outside the `Metric`/`ProvChip` provenance-invariant used by
 * every other view: this is a document snapshot, not a live per-value
 * lineage surface, and its own methodology section (input-snapshot hashes,
 * keys-of-portfolio disclosure) is the provenance story for the whole report.
 */
export function Reports() {
  const tenant = DEFAULT_TENANT;
  const { data, isPending, isError, error, refetch } = useBvr(tenant);

  if (isPending) return <QueryLoading label="Loading Business Value Report…" />;
  if (isError) return <QueryError label="Failed to load Business Value Report" error={error} onRetry={() => refetch()} />;

  const { period, executive_summary: exec, savings, governance, forward_look, methodology } = data;

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-col gap-2 border-b border-line pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold text-ink">Business Value Report</h1>
          <Badge variant="warn" title="Figures are projected against the pre-agent baseline">Projected vs pre-agent baseline</Badge>
        </div>
        <p className="text-sm text-ink-2">
          {period.label} · generated {new Date(period.generated_at).toISOString().slice(0, 10)} · schema {data.schema_version} · {methodology.agent_version}
        </p>
      </header>

      <section aria-label="Executive summary" className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Tile label="Total projected" value={formatAmount(exec.total_projected)} />
        <Tile label="Changes applied" value={String(exec.changes_applied)} />
        <Tile label="Changes shadowed" value={String(exec.changes_shadowed)} />
        <Tile label="Keys under management" value={exec.keys_under_management.toLocaleString("en-US")} />
        <Tile label="Open pipeline" value={formatAmount(exec.open_pipeline_value)} />
        <Tile label="Service" value={exec.service_headline} />
      </section>

      <Card>
        <CardHeader><CardTitle>Savings (projected)</CardTitle></CardHeader>
        <CardContent>
          <ul className="flex flex-col gap-2">
            {SAVINGS_KEYS.map((k) => {
              const c = savings[k];
              return (
                <li key={k} className="flex flex-wrap items-baseline justify-between gap-2 border-t border-line pt-2 text-sm">
                  <span className="text-ink">{savingsComponentLabel(c.name)}</span>
                  <span className="font-medium tabular-nums text-ink">{formatAmount(c.amount)}</span>
                  <span className="w-full text-xs text-ink-3">{c.formula}</span>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 text-xs text-ink-2">
            Applied {formatAmount(savings.total_projected_applied)} · shadowed {formatAmount(savings.total_projected_shadowed)} · total {formatAmount(savings.total_projected)} · {savings.changes_valued}/{savings.changes_total} changes valued
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Governance</CardTitle></CardHeader>
        <CardContent className="text-sm text-ink-2">
          {governance.recommendations_total} recommendations · approval rate {formatRatePct(governance.approval_rate)} · override rate {formatRatePct(governance.override_rate)} · {governance.writes_written} written · {governance.rollbacks} rollbacks{" "}
          <Badge variant={governance.kill_switch_engaged ? "bad" : "good"}>
            Kill switch {governance.kill_switch_engaged ? "engaged" : "off"}
          </Badge>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Forward look</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <p className="text-ink-2">
            Open pipeline {formatAmount(forward_look.open_pipeline_value)} · demand horizon {forward_look.projected_demand_horizon} days
          </p>
          {forward_look.top_opportunities.length === 0 ? (
            <p className="text-ink-2">No open opportunities.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {forward_look.top_opportunities.map((o) => (
                <li key={`${o.pn}/${o.location}`} className="flex flex-wrap items-baseline gap-2">
                  <Link
                    to={`/parts/${encodeURIComponent(o.pn)}/${encodeURIComponent(o.location)}`}
                    className="font-medium text-brand hover:underline"
                  >
                    {o.pn}
                  </Link>
                  <span className="text-ink-2">{o.location} · {o.type}</span>
                  <span className="tabular-nums text-ink">{formatAmount(o.estimated_cost_impact)}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Methodology</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-2 text-xs text-ink-2">
          <p>
            Valued {methodology.keys.toLocaleString("en-US")} of {methodology.keys_total_portfolio.toLocaleString("en-US")} portfolio keys · {methodology.ledger_entries} ledger entries · {methodology.recommendations} recommendations · {methodology.input_snapshot_hash_count} input snapshots · {methodology.agent_version} · {methodology.generated_by}
          </p>
          <ul className="list-disc pl-5">
            {methodology.formulas.map((f) => (<li key={f}>{f}</li>))}
          </ul>
        </CardContent>
      </Card>

      <p className="flex gap-4 text-sm">
        <a href={bffClient.bvrDocumentUrl(tenant, "html")} target="_blank" rel="noreferrer" className="text-brand hover:underline">
          Open printable report
        </a>
        <a href={bffClient.bvrDocumentUrl(tenant, "pdf")} className="text-brand hover:underline">
          Download PDF
        </a>
      </p>
    </div>
  );
}
