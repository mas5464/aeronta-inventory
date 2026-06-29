import type { PlannerTab } from "../hooks/usePlanner";
import styles from "./Tabs.module.css";

interface Props {
  tab: PlannerTab;
  onChange: (tab: PlannerTab) => void;
}

const TABS: { id: PlannerTab; label: string }[] = [
  { id: "pending", label: "Pending" },
  { id: "decided", label: "Decided" },
];

export function Tabs({ tab, onChange }: Props) {
  return (
    <div className={styles.tabs} role="tablist" aria-label="Queue view">
      {TABS.map(({ id, label }) => {
        const selected = id === tab;
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={selected}
            className={`${styles.tab} ${selected ? styles.active : ""}`}
            onClick={() => onChange(id)}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
