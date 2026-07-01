import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric } from "@/components/Metric";
import { HealthMixDonut } from "@/components/HealthMixDonut";
import { AtaRiskList } from "@/components/AtaRiskList";
import { PriorityActionsPreview } from "@/components/PriorityActionsPreview";
import { SlInvestmentPanel } from "@/components/SlInvestmentPanel";
import { useDashboard } from "@/lib/api/useDashboard";
import { dashboardProvenance } from "@/lib/dashboardProvenance";
import { withProvenance } from "@/lib/provenance";

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const integerFormatter = new Intl.NumberFormat("en-US");

function criticalityLabel(key: string): string {
  return `Tier ${key}`;
}

/**
 * Overview — Slice S4: the full dashboard view (PRD §6.1), sourced from
 * GET /v1/tenants/{tenant}/dashboard via useDashboard(). Every displayed
 * number flows through Metric/ProvChip (docs/DESIGN-SYSTEM.md §4). Built on
 * top of S1's "prove the pipe" KPI pair — reuses the same query hook and
 * provenance stamp, just renders the full DashboardSummary.
 */
export function Overview() {
  const { data, isPending, isError, error } = useDashboard();

  if (isPending) {
    return (
      <div role="status" aria-live="polite" className="p-6 text-ink-2">
        Loading dashboard…
      </div>
    );
  }

  if (isError) {
    return (
      <div role="alert" className="p-6 text-bad">
        Failed to load dashboard: {error instanceof Error ? error.message : "unknown error"}
      </div>
    );
  }

  const provenance = dashboardProvenance();

  return (
    <div className="flex flex-col gap-6 p-6">
      <header>
        <h1 className="text-xl font-semibold text-ink">Overview</h1>
        <p className="text-sm text-ink-2">Network inventory health, risk, and priority actions.</p>
      </header>

      {/* KPI cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader>
            <CardTitle>Parts</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric metric={withProvenance(data.parts, provenance)} format={integerFormatter.format} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Total on-hand</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.total_on_hand, provenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>On-hand value</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.total_on_hand_value, provenance)}
              format={currencyFormatter.format}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Total shortage</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.total_shortage, provenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Projected demand</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.total_projected_demand, provenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AOG exposure</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.aog_exposure, provenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Open recommendations</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.open_recommendations, provenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Net cost impact</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.net_cost_impact, provenance)}
              format={currencyFormatter.format}
            />
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Health mix donut */}
        <Card>
          <CardHeader>
            <CardTitle>Inventory health mix</CardTitle>
          </CardHeader>
          <CardContent>
            <HealthMixDonut slices={data.by_criticality} labelFor={criticalityLabel} />
          </CardContent>
        </Card>

        {/* SL-vs-investment (honest gap state) */}
        <Card>
          <CardHeader>
            <CardTitle>Service level vs. investment</CardTitle>
          </CardHeader>
          <CardContent>
            <SlInvestmentPanel byCriticality={data.by_criticality} labelFor={criticalityLabel} />
          </CardContent>
        </Card>

        {/* ATA risk */}
        <Card>
          <CardHeader>
            <CardTitle>Risk by ATA chapter</CardTitle>
          </CardHeader>
          <CardContent>
            <AtaRiskList chapters={data.by_ata} />
          </CardContent>
        </Card>

        {/* Priority actions preview */}
        <Card>
          <CardHeader>
            <CardTitle>Priority actions</CardTitle>
          </CardHeader>
          <CardContent>
            <PriorityActionsPreview shortages={data.top_shortages} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
