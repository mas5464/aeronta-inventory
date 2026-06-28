import { useState } from "react";
import type { AutonomyTier, BulkApproveFilter } from "../api/types";
import styles from "./BulkApproveBar.module.css";

interface Props {
  onBulkApprove: (filter: BulkApproveFilter) => void;
  // True while the agent is paused or a write is in flight — bulk-approve is a write.
  disabled?: boolean;
}

const TIERS: { tier: AutonomyTier; label: string }[] = [
  { tier: 1, label: "Tier A" },
  { tier: 2, label: "Tier B" },
  { tier: 3, label: "Tier C" },
];

// Build the filter, omitting any field the planner left blank ("no constraint").
function buildFilter(tiers: Set<AutonomyTier>, maxDelta: string, minCrit: string): BulkApproveFilter {
  const filter: BulkApproveFilter = {};
  if (tiers.size > 0) filter.tiers = [...tiers].sort((a, b) => a - b);
  if (maxDelta.trim() !== "") filter.max_delta_pct = Number(maxDelta);
  if (minCrit.trim() !== "") filter.criticality_min = Number(minCrit);
  return filter;
}

export function BulkApproveBar({ onBulkApprove, disabled }: Props) {
  const [tiers, setTiers] = useState<Set<AutonomyTier>>(new Set());
  const [maxDelta, setMaxDelta] = useState("");
  const [minCrit, setMinCrit] = useState("");

  const toggleTier = (tier: AutonomyTier) =>
    setTiers((prev) => {
      const next = new Set(prev);
      next.has(tier) ? next.delete(tier) : next.add(tier);
      return next;
    });

  return (
    <section className={styles.bar} aria-label="Bulk approve">
      <span className={styles.legend}>Bulk approve</span>

      <fieldset className={styles.tiers}>
        <legend className={styles.srOnly}>Autonomy tiers</legend>
        {TIERS.map(({ tier, label }) => (
          <label key={tier} className={styles.check}>
            <input
              type="checkbox"
              checked={tiers.has(tier)}
              onChange={() => toggleTier(tier)}
              aria-label={label}
            />
            {label}
          </label>
        ))}
      </fieldset>

      <label className={styles.field}>
        Max change %
        <input
          type="number"
          min="0"
          inputMode="numeric"
          value={maxDelta}
          onChange={(e) => setMaxDelta(e.target.value)}
        />
      </label>

      <label className={styles.field}>
        Min criticality
        <input
          type="number"
          min="1"
          max="5"
          inputMode="numeric"
          value={minCrit}
          onChange={(e) => setMinCrit(e.target.value)}
        />
      </label>

      <button
        type="button"
        className={styles.go}
        disabled={disabled}
        onClick={() => onBulkApprove(buildFilter(tiers, maxDelta, minCrit))}
      >
        Approve matching
      </button>
    </section>
  );
}
