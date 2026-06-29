import { TIER_LABEL, type AutonomyTier, type QueueRow } from "../api/types";
import { money, priority, typeLabel } from "../lib/format";
import styles from "./QueueTable.module.css";

interface Props {
  rows: QueueRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onApprove: (id: string) => void;
  disabled?: boolean;
  // An approve/reject/defer write is in flight — gate every approve to prevent double-submits.
  busy?: boolean;
  // Decided view: rows are already resolved — show a status badge, not an approve action.
  decided?: boolean;
}

const TIER_CLASS: Record<AutonomyTier, string> = {
  1: styles.tierA,
  2: styles.tierB,
  3: styles.tierC,
};

export function QueueTable({
  rows,
  selectedId,
  onSelect,
  onApprove,
  disabled,
  busy,
  decided,
}: Props) {
  if (rows.length === 0) {
    return (
      <p className={styles.empty}>
        {decided
          ? "No decided recommendations yet."
          : "No pending recommendations. You're all caught up."}
      </p>
    );
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
          {decided ? <th>Status</th> : <th aria-label="actions" />}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const selected = r.recommendation_id === selectedId;
          const approveDisabled = disabled || !r.approvable || busy;
          return (
            <tr key={r.recommendation_id} className={selected ? styles.selected : undefined}>
              <td>
                <button
                  type="button"
                  className={styles.select}
                  aria-pressed={selected}
                  onClick={() => onSelect(r.recommendation_id)}
                >
                  <span
                    className={styles.dot}
                    style={{ background: `var(--crit-${r.criticality_tier})` }}
                    aria-hidden="true"
                  />
                  <span className={styles.srOnly}>Criticality {r.criticality_tier}. </span>
                  <span className={styles.pn}>{r.pn}</span>
                  <span className={styles.loc}> · {r.location}</span>
                </button>
              </td>
              <td className={styles.type}>{typeLabel(r.type)}</td>
              <td>
                <span className={`${styles.tier} ${TIER_CLASS[r.tier]}`}>{TIER_LABEL[r.tier]}</span>
              </td>
              <td className={`${styles.num} ${styles.prio}`}>{priority(r.priority_score)}</td>
              <td className={styles.num}>{money(r.estimated_cost_impact)}</td>
              <td className={styles.actions}>
                {decided ? (
                  <span className={`${styles.status} ${styles[`status_${r.status}`] ?? ""}`}>
                    {r.status}
                  </span>
                ) : (
                  <button
                    type="button"
                    disabled={approveDisabled}
                    title={
                      !r.approvable
                        ? "Advisory recommendation — nothing to write"
                        : disabled
                          ? "Approvals are paused — resume the agent to approve"
                          : undefined
                    }
                    onClick={() => onApprove(r.recommendation_id)}
                  >
                    Approve
                  </button>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
