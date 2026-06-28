import { TIER_LABEL, type AutonomyTier, type QueueRow } from "../api/types";
import { money, priority, typeLabel } from "../lib/format";
import styles from "./QueueTable.module.css";

interface Props {
  rows: QueueRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onApprove: (id: string) => void;
  disabled?: boolean;
}

const TIER_CLASS: Record<AutonomyTier, string> = {
  1: styles.tierA,
  2: styles.tierB,
  3: styles.tierC,
};

export function QueueTable({ rows, selectedId, onSelect, onApprove, disabled }: Props) {
  if (rows.length === 0) {
    return <p className={styles.empty}>No pending recommendations. You're all caught up.</p>;
  }
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th>Part · location</th>
          <th>Type</th>
          <th>Tier</th>
          <th className={styles.num}>Priority</th>
          <th className={styles.num}>Cost impact</th>
          <th aria-label="actions" />
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const selected = r.recommendation_id === selectedId;
          return (
            <tr
              key={r.recommendation_id}
              className={selected ? styles.selected : undefined}
              aria-selected={selected}
              onClick={() => onSelect(r.recommendation_id)}
            >
              <td>
                <span
                  className={styles.dot}
                  style={{ background: `var(--crit-${r.criticality_tier})` }}
                  title={`criticality ${r.criticality_tier}`}
                  aria-hidden="true"
                />
                <span className={styles.pn}>{r.pn}</span>
                <span className={styles.loc}> · {r.location}</span>
              </td>
              <td className={styles.type}>{typeLabel(r.type)}</td>
              <td>
                <span className={`${styles.tier} ${TIER_CLASS[r.tier]}`}>{TIER_LABEL[r.tier]}</span>
              </td>
              <td className={`${styles.num} ${styles.prio}`}>{priority(r.priority_score)}</td>
              <td className={styles.num}>{money(r.estimated_cost_impact)}</td>
              <td className={styles.actions}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={(e) => {
                    e.stopPropagation();
                    onApprove(r.recommendation_id);
                  }}
                >
                  Approve
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
