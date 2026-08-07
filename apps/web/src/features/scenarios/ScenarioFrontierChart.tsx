import type { FrontierPoint, ScenarioOutcome } from "@/lib/api/types";

export interface ScenarioFrontierChartProps {
  frontier: FrontierPoint[];
  current: ScenarioOutcome;
  proposed: ScenarioOutcome;
}

const pctFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

/**
 * Dependency-free inline-SVG cost-service frontier (PRD §6.5 "Cost–service
 * trade-off frontier with the scenario marker") — mirrors DemandTrend/HealthMixDonut's
 * approach (no charting library). Plots `projected_investment` (x) vs. `service_level`
 * (y) for each solved frontier point, connected by a line, with distinct current-plan
 * and proposed-scenario markers overlaid.
 */
export function ScenarioFrontierChart({
  frontier,
  current,
  proposed,
}: ScenarioFrontierChartProps) {
  if (frontier.length === 0) {
    return <p className="text-sm text-ink-2">No frontier data available.</p>;
  }

  const width = 480;
  const height = 220;
  const padding = { top: 16, right: 16, bottom: 32, left: 48 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  const allInvestments = [
    ...frontier.map((p) => p.projected_investment),
    current.projected_investment,
    proposed.projected_investment,
  ];
  const allServiceLevels = [
    ...frontier.map((p) => p.service_level),
    current.service_level,
    proposed.service_level,
  ];
  const minInv = Math.min(...allInvestments);
  const maxInv = Math.max(...allInvestments, minInv + 1);
  const minSl = Math.min(...allServiceLevels, 0.85);
  const maxSl = Math.max(...allServiceLevels, 1.0);

  const x = (inv: number) =>
    padding.left + ((inv - minInv) / (maxInv - minInv || 1)) * plotWidth;
  const y = (sl: number) =>
    padding.top + plotHeight - ((sl - minSl) / (maxSl - minSl || 1)) * plotHeight;

  const sorted = [...frontier].sort((a, b) => a.projected_investment - b.projected_investment);
  const linePath = sorted
    .map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.projected_investment)} ${y(p.service_level)}`)
    .join(" ");

  const summary = frontier
    .map(
      (p) =>
        `${pctFormatter.format(p.service_level)} service level at ${compactCurrencyFormatter.format(p.projected_investment)}`,
    )
    .join("; ");

  return (
    <div className="flex flex-col gap-2">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        role="img"
        aria-label={`Cost-service frontier. ${summary}. Current plan at ${pctFormatter.format(current.service_level)} service level, ${compactCurrencyFormatter.format(current.projected_investment)} investment. Proposed scenario at ${pctFormatter.format(proposed.service_level)} service level, ${compactCurrencyFormatter.format(proposed.projected_investment)} investment.`}
        className="h-[220px] w-full"
      >
        {/* Axes */}
        <line
          x1={padding.left}
          y1={padding.top}
          x2={padding.left}
          y2={height - padding.bottom}
          className="stroke-line"
          strokeWidth={1}
        />
        <line
          x1={padding.left}
          y1={height - padding.bottom}
          x2={width - padding.right}
          y2={height - padding.bottom}
          className="stroke-line"
          strokeWidth={1}
        />

        {/* Frontier line + points */}
        <path d={linePath} fill="none" className="stroke-series-1" strokeWidth={2} />
        {sorted.map((p) => (
          <circle
            key={p.service_level}
            cx={x(p.projected_investment)}
            cy={y(p.service_level)}
            r={3}
            className="fill-series-1"
          />
        ))}

        {/* Current-plan marker */}
        <circle
          cx={x(current.projected_investment)}
          cy={y(current.service_level)}
          r={6}
          className="fill-panel stroke-ink-2"
          strokeWidth={2}
          data-testid="frontier-current-marker"
        />

        {/* Proposed-scenario marker */}
        <circle
          cx={x(proposed.projected_investment)}
          cy={y(proposed.service_level)}
          r={6}
          className="fill-good stroke-ink"
          strokeWidth={2}
          data-testid="frontier-proposed-marker"
        />
      </svg>
      <ul className="flex flex-wrap gap-4 text-xs text-ink-2" aria-hidden="true">
        <li className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full border-2 border-ink-2 bg-panel" />
          Current plan
        </li>
        <li className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full border-2 border-ink bg-good" />
          Proposed scenario
        </li>
        <li className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-series-1" />
          Frontier point
        </li>
      </ul>
    </div>
  );
}
