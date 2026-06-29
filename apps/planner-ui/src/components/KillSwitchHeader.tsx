import type { KillSwitchState } from "../api/types";
import styles from "./KillSwitchHeader.module.css";

interface Props {
  tenant: string;
  count: number;
  countLabel?: string;
  state: KillSwitchState;
  onToggle: (engaged: boolean) => void;
}

export function KillSwitchHeader({ tenant, count, countLabel = "pending", state, onToggle }: Props) {
  const { engaged } = state;
  return (
    <header className={styles.header}>
      <div>
        <h1 className={styles.title}>Trax IO Review</h1>
        <div className={styles.sub}>
          {tenant} · {count} {countLabel}
        </div>
      </div>
      <button
        type="button"
        className={engaged ? styles.paused : styles.active}
        onClick={() => onToggle(!engaged)}
        aria-pressed={engaged}
        aria-label={engaged ? "Resume agent" : "Pause agent (kill switch)"}
      >
        <span className={styles.dot} aria-hidden="true" />
        {engaged ? "Agent paused" : "Agent active"}
      </button>
    </header>
  );
}
