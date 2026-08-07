import type { Breakdown } from "@/lib/api/types";

export interface HealthMixDonutProps {
  /** Breakdown slices (e.g. `by_criticality` or `by_tier`) to render as a donut by count. */
  slices: Breakdown[];
  /** Human label for a slice key, e.g. tier number -> "Tier 1". Defaults to the raw key. */
  labelFor?: (key: string) => string;
}

// Stroke classes, not fill — the arcs are stroked rings (fill="none"). A
// fill-* class here silently overrides the fill="none" attribute and paints
// the last slice as a solid disc over the whole donut (the pre-redesign bug).
const SLICE_COLORS = [
  "stroke-series-1",
  "stroke-good",
  "stroke-warn",
  "stroke-bad",
  "stroke-ink-3",
] as const;

/**
 * Dependency-free inline-SVG donut — mirrors DemandTrend's approach (no
 * charting library). Renders `slices` (by count) as a ring of arcs, plus an
 * accessible legend (never color-only — each slice pairs its color swatch
 * with a text label and value, per docs/DESIGN-SYSTEM.md §4/WCAG).
 */
export function HealthMixDonut({ slices, labelFor }: HealthMixDonutProps) {
  const total = slices.reduce((sum, s) => sum + s.count, 0);

  if (slices.length === 0 || total === 0) {
    return <p className="text-sm text-ink-2">No health-mix data available.</p>;
  }

  const size = 160;
  const radius = 60;
  const strokeWidth = 24;
  const center = size / 2;
  const circumference = 2 * Math.PI * radius;

  let offsetSoFar = 0;
  const arcs = slices.map((slice, i) => {
    const fraction = slice.count / total;
    const dash = fraction * circumference;
    const arc = {
      key: slice.key,
      dashArray: `${dash} ${circumference - dash}`,
      dashOffset: -offsetSoFar,
      colorClass: SLICE_COLORS[i % SLICE_COLORS.length],
      fraction,
    };
    offsetSoFar += dash;
    return arc;
  });

  const summary = slices
    .map((s) => `${labelFor ? labelFor(s.key) : s.key}: ${s.count} (${Math.round((s.count / total) * 100)}%)`)
    .join(", ");

  return (
    <div className="flex flex-wrap items-center gap-4">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="img"
        aria-label={`Inventory health mix by count. ${summary}.`}
      >
        <g transform={`rotate(-90 ${center} ${center})`}>
          {arcs.map((arc) => (
            <circle
              key={arc.key}
              cx={center}
              cy={center}
              r={radius}
              fill="none"
              strokeWidth={strokeWidth}
              strokeDasharray={arc.dashArray}
              strokeDashoffset={arc.dashOffset}
              className={arc.colorClass}
            />
          ))}
        </g>
        <text
          x={center}
          y={center}
          textAnchor="middle"
          dominantBaseline="middle"
          className="fill-ink text-sm font-semibold"
        >
          {total.toLocaleString()}
        </text>
      </svg>
      <ul className="flex flex-col gap-1 text-sm" aria-hidden="true">
        {arcs.map((arc, i) => (
          <li key={arc.key} className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${SLICE_COLORS[i % SLICE_COLORS.length].replace("stroke-", "bg-")}`} />
            <span className="text-ink">{labelFor ? labelFor(arc.key) : arc.key}</span>
            <span className="text-ink-2">
              {slices[i].count.toLocaleString()} ({Math.round(arc.fraction * 100)}%)
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
