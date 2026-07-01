import type { DemandPoint } from "@/lib/api/types";

export interface DemandTrendProps {
  points: DemandPoint[];
}

/**
 * Dependency-free inline-SVG demand trend (mirrors apps/planner-ui's
 * DemandTrend approach — small bar chart, no charting library).
 */
export function DemandTrend({ points }: DemandTrendProps) {
  if (points.length === 0) {
    return <p className="text-sm text-ink-2">No demand history for this part.</p>;
  }

  const max = Math.max(1, ...points.map((p) => p.total));
  const width = 320;
  const height = 90;
  const barWidth = width / points.length;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      role="img"
      aria-label="Demand history trend"
      className="h-[90px] w-full"
    >
      {points.map((p, i) => {
        const barHeight = (p.total / max) * (height - 16);
        return (
          <rect
            key={p.period_start}
            x={i * barWidth + 1}
            y={height - barHeight}
            width={Math.max(1, barWidth - 2)}
            height={barHeight}
            className="fill-brand"
          />
        );
      })}
    </svg>
  );
}
