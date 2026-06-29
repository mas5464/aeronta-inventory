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

// Shared so the App can mark the queue area as this tablist's controlled panel.
export const QUEUE_PANEL_ID = "queue-tabpanel";
export const queueTabId = (id: PlannerTab) => `tab-${id}`;

export function Tabs({ tab, onChange }: Props) {
  // Arrow-key navigation with automatic activation + roving focus (WAI-ARIA tabs pattern).
  const onKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const idx = TABS.findIndex((t) => t.id === tab);
    const delta = e.key === "ArrowRight" ? 1 : -1;
    const next = TABS[(idx + delta + TABS.length) % TABS.length];
    onChange(next.id);
    const buttons = e.currentTarget.parentElement?.querySelectorAll<HTMLElement>('[role="tab"]');
    buttons?.[(idx + delta + TABS.length) % TABS.length]?.focus();
  };

  return (
    <div className={styles.tabs} role="tablist" aria-label="Queue view">
      {TABS.map(({ id, label }) => {
        const selected = id === tab;
        return (
          <button
            key={id}
            id={queueTabId(id)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={QUEUE_PANEL_ID}
            tabIndex={selected ? 0 : -1}
            className={`${styles.tab} ${selected ? styles.active : ""}`}
            onClick={() => onChange(id)}
            onKeyDown={onKeyDown}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
