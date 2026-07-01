import { TIER_LABEL, type AutonomyTier, type QueueRow } from "../api/types";
import { typeLabel } from "../lib/format";
import styles from "./ChartRow.module.css";

interface Props {
  rows: QueueRow[];
}

// Fixed categorical colors (mid-ramp hexes read on both light and dark backgrounds).
const TYPE_COLORS: Record<string, string> = {
  transfer: "#378ADD",
  purchase: "#639922",
  adjust_min_max: "#BA7517",
  reduce_stock: "#D4537E",
  sell: "#7F77DD",
};
const OTHER = "#888780";

function countBy<T extends string | number>(rows: QueueRow[], key: (r: QueueRow) => T) {
  const m = new Map<T, number>();
  for (const r of rows) m.set(key(r), (m.get(key(r)) ?? 0) + 1);
  return m;
}

export function ChartRow({ rows }: Props) {
  const total = rows.length;
  const byType = [...countBy(rows, (r) => r.type).entries()].sort((a, b) => b[1] - a[1]);
  const byTier = countBy(rows, (r) => r.tier);
  const tierMax = Math.max(1, ...byTier.values());

  const R = 34;
  const C = 2 * Math.PI * R;
  let offset = 0;

  return (
    <div className={styles.row}>
      <div className={styles.card}>
        <div className={styles.title}>By type</div>
        <div className={styles.donutWrap}>
          <svg viewBox="0 0 80 80" width="80" height="80" role="img" aria-label="Recommendations by type">
            <circle cx="40" cy="40" r={R} fill="none" stroke="var(--border)" strokeWidth="12" />
            {total > 0 &&
              byType.map(([type, n]) => {
                const len = (n / total) * C;
                const el = (
                  <circle
                    key={type}
                    cx="40"
                    cy="40"
                    r={R}
                    fill="none"
                    stroke={TYPE_COLORS[type] ?? OTHER}
                    strokeWidth="12"
                    strokeDasharray={`${len} ${C - len}`}
                    strokeDashoffset={-offset}
                    transform="rotate(-90 40 40)"
                  />
                );
                offset += len;
                return el;
              })}
            <text x="40" y="44" textAnchor="middle" className={styles.donutCenter}>
              {total}
            </text>
          </svg>
          <ul className={styles.legend}>
            {byType.map(([type, n]) => (
              <li key={type}>
                <span
                  className={styles.swatch}
                  style={{ background: TYPE_COLORS[type] ?? OTHER }}
                  aria-hidden="true"
                />
                <span className={styles.legendLabel}>{typeLabel(type)}</span>{" "}
                <span className={styles.legendCount}>{n}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className={styles.card}>
        <div className={styles.title}>By tier</div>
        <ul className={styles.bars}>
          {([1, 2, 3] as AutonomyTier[]).map((tier) => {
            const n = byTier.get(tier) ?? 0;
            return (
              <li key={tier} className={styles.barRow}>
                <span className={styles.barLabel}>Tier {TIER_LABEL[tier]}</span>
                <span className={styles.barTrack}>
                  <span className={styles.barFill} style={{ width: `${(n / tierMax) * 100}%` }} />
                </span>
                <span className={styles.barCount}>{n}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
