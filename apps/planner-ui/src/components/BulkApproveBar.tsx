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

// Recommendation types (mirrors the engine's RecommendationType). The order is preserved
// in the emitted filter so it's stable/testable.
const TYPES: { type: string; label: string }[] = [
  { type: "transfer", label: "Transfer" },
  { type: "purchase", label: "Purchase" },
  { type: "adjust_min_max", label: "Adjust min/max" },
  { type: "reduce_stock", label: "Reduce stock" },
  { type: "sell", label: "Sell" },
];

// Build the filter, omitting any field the planner left blank ("no constraint").
function buildFilter(
  tiers: Set<AutonomyTier>,
  types: Set<string>,
  maxDelta: string,
  minCrit: string,
): BulkApproveFilter {
  const filter: BulkApproveFilter = {};
  if (tiers.size > 0) filter.tiers = [...tiers].sort((a, b) => a - b);
  if (types.size > 0) filter.types = TYPES.map((t) => t.type).filter((t) => types.has(t));
  if (maxDelta.trim() !== "") filter.max_delta_pct = Number(maxDelta);
  if (minCrit.trim() !== "") filter.criticality_min = Number(minCrit);
  return filter;
}

export function BulkApproveBar({ onBulkApprove, disabled }: Props) {
  const [tiers, setTiers] = useState<Set<AutonomyTier>>(new Set());
  const [types, setTypes] = useState<Set<string>>(new Set());
  const [maxDelta, setMaxDelta] = useState("");
  const [minCrit, setMinCrit] = useState("");

  const toggleTier = (tier: AutonomyTier) =>
    setTiers((prev) => {
      const next = new Set(prev);
      next.has(tier) ? next.delete(tier) : next.add(tier);
      return next;
    });

  const toggleType = (type: string) =>
    setTypes((prev) => {
      const next = new Set(prev);
      next.has(type) ? next.delete(type) : next.add(type);
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

      <fieldset className={styles.tiers}>
        <legend className={styles.srOnly}>Recommendation types</legend>
        {TYPES.map(({ type, label }) => (
          <label key={type} className={styles.check}>
            <input
              type="checkbox"
              checked={types.has(type)}
              onChange={() => toggleType(type)}
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
        onClick={() => onBulkApprove(buildFilter(tiers, types, maxDelta, minCrit))}
      >
        Approve matching
      </button>
    </section>
  );
}
