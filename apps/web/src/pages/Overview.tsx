import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric } from "@/components/Metric";
import { useDashboard } from "@/lib/api/useDashboard";
import { dashboardProvenance } from "@/lib/dashboardProvenance";
import { withProvenance } from "@/lib/provenance";

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const integerFormatter = new Intl.NumberFormat("en-US");

/**
 * Overview-lite — Slice S1 "prove the pipe": renders one real KPI pair
 * (Parts, Net cost impact) sourced from GET /v1/tenants/{tenant}/dashboard,
 * via the Metric + ProvChip primitives. Loading/error states included.
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
    <div className="grid grid-cols-1 gap-4 p-6 sm:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Parts</CardTitle>
        </CardHeader>
        <CardContent>
          <Metric
            metric={withProvenance(data.parts, provenance)}
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
  );
}
