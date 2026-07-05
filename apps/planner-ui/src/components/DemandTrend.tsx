import type { DemandPoint } from "../api/types";
import styles from "./DemandTrend.module.css";

const W = 320;
const CHART_H = 66;
const PAD_X = 12;
const BAR_W = 10;
const YEAR_MS = 365.25 * 24 * 60 * 60 * 1000;

function monthYear(d: Date): string {
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

export function DemandTrend({ points }: { points: DemandPoint[] }) {
  if (points.length === 0) return <p className={styles.empty}>No demand history for this part.</p>;

  const times = points.map((p) => new Date(p.period_start).getTime());
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  const span = maxT - minT;
  const usableW = W - PAD_X * 2;
  const max = Math.max(1, ...points.map((p) => p.total));
  const xFor = (t: number) => PAD_X + (span === 0 ? 0.5 : (t - minT) / span) * usableW;

  const stepMs = span >= 2 * YEAR_MS ? YEAR_MS : YEAR_MS / 2;
  const gridlineTimes: number[] = [];
  if (span > 0) {
    for (let t = minT; t < maxT; t += stepMs) gridlineTimes.push(t);
    gridlineTimes.push(maxT);
  }

  return (
    <>
      <svg
        viewBox={`0 0 ${W} ${CHART_H + 24}`}
        width="100%"
        role="img"
        aria-label="Demand history trend"
        className={styles.chart}
      >
        {gridlineTimes.map((t) => (
          <line key={t} x1={xFor(t)} y1={0} x2={xFor(t)} y2={CHART_H} className={styles.gridline} />
        ))}
        {points.map((p, i) => {
          const h = (p.total / max) * (CHART_H - 8);
          return (
            <rect
              key={p.period_start}
              x={xFor(times[i]) - BAR_W / 2}
              y={CHART_H - h}
              width={BAR_W}
              height={h}
              className={styles.bar}
            />
          );
        })}
      </svg>
      <p className={styles.caption}>
        Demand history: {monthYear(new Date(minT))}
        {span > 0 ? ` – ${monthYear(new Date(maxT))}` : ""}
      </p>
    </>
  );
}
