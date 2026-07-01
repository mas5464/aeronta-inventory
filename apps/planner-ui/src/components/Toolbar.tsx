import { Check, Download } from "lucide-react";
import type { AutonomyTier } from "../api/types";
import type { QueueFilter } from "../lib/queryView";
import styles from "./Toolbar.module.css";

interface Props {
  filter: QueueFilter;
  onFilterChange: (filter: QueueFilter) => void;
  onExport: () => void;
  // "Approve matching" bulk-approves the rows matching the current tier/type filter
  // (server-side); omitted on the read-only Decided view.
  onBulkApprove?: () => void;
  bulkDisabled?: boolean;
}

const TIERS: { tier: AutonomyTier; label: string }[] = [
  { tier: 1, label: "Tier A" },
  { tier: 2, label: "Tier B" },
  { tier: 3, label: "Tier C" },
];

const TYPES = ["transfer", "purchase", "adjust_min_max", "reduce_stock", "sell"];
const TYPE_LABELS: Record<string, string> = {
  transfer: "Transfer",
  purchase: "Purchase",
  adjust_min_max: "Adjust min/max",
  reduce_stock: "Reduce stock",
  sell: "Sell",
};
const AOG_LEVELS: { value: number; label: string }[] = [
  { value: 2, label: "Medium+" },
  { value: 3, label: "High+" },
  { value: 4, label: "Critical" },
];

export function Toolbar({ filter, onFilterChange, onExport, onBulkApprove, bulkDisabled }: Props) {
  // Merge a partial change onto the current filter, pruning empty constraints.
  const patch = (p: Partial<QueueFilter>) => {
    const next: QueueFilter = { ...filter, ...p };
    if (!next.search) delete next.search;
    if (!next.tiers?.length) delete next.tiers;
    if (!next.types?.length) delete next.types;
    if (next.aogMin == null) delete next.aogMin;
    onFilterChange(next);
  };

  const toggleTier = (tier: AutonomyTier) => {
    const set = new Set(filter.tiers ?? []);
    set.has(tier) ? set.delete(tier) : set.add(tier);
    patch({ tiers: [...set].sort((a, b) => a - b) });
  };

  return (
    <div className={styles.bar}>
      <label className={styles.search}>
        <span className={styles.srOnly}>Search part or location</span>
        <input
          type="search"
          aria-label="Search part or location"
          placeholder="Search part or location"
          value={filter.search ?? ""}
          onChange={(e) => patch({ search: e.target.value })}
        />
      </label>

      <fieldset className={styles.tiers}>
        <legend className={styles.srOnly}>Filter by tier</legend>
        {TIERS.map(({ tier, label }) => (
          <label key={tier} className={styles.chip}>
            <input
              type="checkbox"
              checked={filter.tiers?.includes(tier) ?? false}
              onChange={() => toggleTier(tier)}
              aria-label={label}
            />
            {label}
          </label>
        ))}
      </fieldset>

      <label className={styles.field}>
        <span className={styles.srOnly}>Type</span>
        <select
          aria-label="Type"
          value={filter.types?.[0] ?? ""}
          onChange={(e) => patch({ types: e.target.value ? [e.target.value] : undefined })}
        >
          <option value="">All types</option>
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </label>

      <label className={styles.field}>
        <span className={styles.srOnly}>AOG risk</span>
        <select
          aria-label="AOG risk"
          value={filter.aogMin ?? ""}
          onChange={(e) =>
            patch({ aogMin: e.target.value ? (Number(e.target.value) as QueueFilter["aogMin"]) : undefined })
          }
        >
          <option value="">Any AOG risk</option>
          {AOG_LEVELS.map((l) => (
            <option key={l.value} value={l.value}>
              {l.label}
            </option>
          ))}
        </select>
      </label>

      <button type="button" className={styles.export} onClick={onExport}>
        <Download size={14} aria-hidden="true" /> Export
      </button>

      {onBulkApprove && (
        <button
          type="button"
          className={styles.bulk}
          disabled={bulkDisabled}
          onClick={onBulkApprove}
          title="Approve every pending recommendation matching the current tier and type filter"
        >
          <Check size={14} aria-hidden="true" /> Approve matching
        </button>
      )}
    </div>
  );
}
