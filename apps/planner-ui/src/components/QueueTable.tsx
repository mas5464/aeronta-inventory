import { ArrowDown, ArrowUp } from "lucide-react";
import { AOG_LABEL, TIER_LABEL, type AutonomyTier, type QueueRow } from "../api/types";
import { confidenceTier, type ConfidenceTier } from "../lib/confidenceTier";
import type { SortKey, SortSpec } from "../lib/queryView";
import { money, priority, typeLabel } from "../lib/format";
import styles from "./QueueTable.module.css";

interface Props {
  rows: QueueRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onApprove: (id: string) => void;
  disabled?: boolean;
  busy?: boolean;
  decided?: boolean;
  sort?: SortSpec;
  onSort?: (key: SortKey) => void;
}

const TIER_CLASS: Record<AutonomyTier, string> = {
  1: styles.tierA,
  2: styles.tierB,
  3: styles.tierC,
};

const CONF_CLASS: Record<ConfidenceTier, string> = {
  high: styles.confHigh,
  medium: styles.confMedium,
  low: styles.confLow,
};

// Order here drives BOTH the header and the body cells (below), so the two can't
// drift out of alignment. Part is the clickable selector; Location and Description
// are their own columns.
const COLUMNS: { key: SortKey; label: string; num?: boolean }[] = [
  { key: "pn", label: "Part" },
  { key: "location", label: "Location" },
  { key: "description", label: "Description" },
  { key: "current_stock", label: "On hand", num: true },
  { key: "shortage_quantity", label: "Need", num: true },
  { key: "type", label: "Type" },
  { key: "tier", label: "Tier" },
  { key: "aog_risk_level", label: "AOG" },
  { key: "confidence_score", label: "Conf." },
  { key: "estimated_cost_impact", label: "Cost impact", num: true },
  { key: "priority_score", label: "Priority", num: true },
];

function aogClass(level: number): string {
  if (level >= 3) return styles.aogHigh;
  if (level === 2) return styles.aogMed;
  return styles.aogLow;
}

export function QueueTable({
  rows,
  selectedId,
  onSelect,
  onApprove,
  disabled,
  busy,
  decided,
  sort,
  onSort,
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

  const header = (col: { key: SortKey; label: string; num?: boolean }) => {
    const active = sort?.key === col.key;
    const Arrow = sort?.dir === "asc" ? ArrowUp : ArrowDown;
    const inner = (
      <>
        {col.label}
        {active && <Arrow size={12} aria-hidden="true" className={styles.sortIcon} />}
      </>
    );
    return (
      <th key={col.key} className={col.num ? styles.num : undefined} aria-sort={active ? (sort?.dir === "asc" ? "ascending" : "descending") : undefined}>
        {onSort ? (
          <button type="button" className={styles.sortBtn} onClick={() => onSort(col.key)}>
            {inner}
          </button>
        ) : (
          inner
        )}
      </th>
    );
  };

  return (
    <table className={styles.table}>
      <thead>
        <tr>
          {COLUMNS.map(header)}
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
                </button>
              </td>
              <td className={styles.loc}>{r.location}</td>
              <td className={styles.desc} title={r.description}>
                {r.description}
              </td>
              <td className={styles.num}>{r.current_stock}</td>
              <td className={styles.num}>{Math.round(r.shortage_quantity)}</td>
              <td className={styles.type}>{typeLabel(r.type)}</td>
              <td>
                <span className={`${styles.tier} ${TIER_CLASS[r.tier]}`}>{TIER_LABEL[r.tier]}</span>
              </td>
              <td>
                <span className={`${styles.aog} ${aogClass(r.aog_risk_level)}`}>
                  {AOG_LABEL[r.aog_risk_level]}
                </span>
              </td>
              <td>
                <span className={`${styles.conf} ${CONF_CLASS[confidenceTier(r.confidence_score)]}`}>
                  {Math.round(r.confidence_score * 100)}%
                </span>
              </td>
              <td className={styles.num}>{money(r.estimated_cost_impact)}</td>
              <td className={`${styles.num} ${styles.prio}`}>{priority(r.priority_score)}</td>
              <td className={styles.actions}>
                {decided ? (
                  <span className={`${styles.status} ${styles[`status_${r.status}`] ?? ""}`}>
                    {r.status}
                  </span>
                ) : (
                  <button
                    type="button"
                    className={styles.approve}
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
