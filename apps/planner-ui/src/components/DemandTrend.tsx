import type { DemandPoint } from "../api/types";
import styles from "./DemandTrend.module.css";

export function DemandTrend({ points }: { points: DemandPoint[] }) {
  if (points.length === 0) return <p className={styles.empty}>No demand history for this part.</p>;
  const max = Math.max(1, ...points.map((p) => p.total));
  const W = 320, H = 90, bw = W / points.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Demand history trend" className={styles.chart}>
      {points.map((p, i) => {
        const h = (p.total / max) * (H - 16);
        return <rect key={p.period_start} x={i * bw + 1} y={H - h} width={Math.max(1, bw - 2)} height={h} className={styles.bar} />;
      })}
    </svg>
  );
}
