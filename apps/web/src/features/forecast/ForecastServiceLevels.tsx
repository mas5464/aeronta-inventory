import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric } from "@/components/Metric";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { useForecast } from "@/lib/api/useForecast";
import {
  accuracyProvenance,
  methodCoverageProvenance,
  serviceLevelProvenance,
} from "@/lib/forecastProvenance";
import { withProvenance } from "@/lib/provenance";
import { ServiceLevelTable } from "@/features/forecast/ServiceLevelTable";
import { MethodCoverageBars } from "@/features/forecast/MethodCoverageBars";
import { AccuracyBand } from "@/features/forecast/AccuracyBand";

const integerFormatter = new Intl.NumberFormat("en-US");

/**
 * Forecast & Service Levels — Slice S5 (PRD §6.6), sourced from
 * GET /v1/tenants/{tenant}/forecast via useForecast(). Every displayed number flows
 * through Metric/ProvChip (docs/DESIGN-SYSTEM.md §4).
 *
 * REAL: differentiated SL policy by criticality (TenantPolicyConfig crossed with real
 * key counts) and forecast-method coverage (the engine's deterministic regime
 * classifier run over every key's real DEMAND_HISTORY). HONEST GAP: forecast accuracy
 * / actual-vs-forecast band — no backtest runs at serve time, so the accuracy section
 * always surfaces its "not yet connected" banner alongside the one truthful proxy
 * available (recent actuals vs. current projection).
 */
export function ForecastServiceLevels() {
  const { data, isPending, isError, error, refetch, dataUpdatedAt } = useForecast();

  if (isPending) {
    return <QueryLoading label="Loading forecast…" />;
  }

  if (isError) {
    return <QueryError label="Failed to load forecast" error={error} onRetry={() => refetch()} />;
  }

  // Real fetch time, not render-time "now" — see Overview.tsx for why.
  const asOf = new Date(dataUpdatedAt);
  const slProvenance = serviceLevelProvenance(asOf);
  const methodProvenance = methodCoverageProvenance(asOf);
  const accProvenance = accuracyProvenance(asOf);

  return (
    <div className="flex flex-col gap-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold text-ink">Forecast & Service Levels</h1>
        <p className="text-sm text-ink-2">
          Differentiated service-level policy, forecast-method coverage, and
          actual-vs-forecast demand across the network.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>SKUs on ML/statistical forecast</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.method_coverage.total_skus, methodProvenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>SL policy tiers configured</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.service_levels.bands.length, slProvenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Service-level policy by criticality</CardTitle>
          </CardHeader>
          <CardContent>
            <ServiceLevelTable bands={data.service_levels.bands} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Forecast-method coverage</CardTitle>
          </CardHeader>
          <CardContent>
            <MethodCoverageBars
              rows={data.method_coverage.rows}
              totalSkus={data.method_coverage.total_skus}
            />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Network actual vs. forecast</CardTitle>
          </CardHeader>
          <CardContent>
            <AccuracyBand accuracy={data.accuracy} provenance={accProvenance} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
