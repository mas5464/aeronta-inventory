import { ClipboardCheck, History, LayoutDashboard, Settings } from "lucide-react";
import styles from "./NavRail.module.css";

// App-shell navigation. "Review" is the live section (the approval queue); the others
// are placeholders for future sections, shown disabled so the shell reads as a system.
const ITEMS: { id: string; label: string; icon: typeof ClipboardCheck; live?: boolean }[] = [
  { id: "review", label: "Review", icon: ClipboardCheck, live: true },
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "writebacks", label: "Writebacks", icon: History },
  { id: "settings", label: "Settings", icon: Settings },
];

export function NavRail() {
  return (
    <nav className={styles.rail} aria-label="Sections">
      <div className={styles.brand} aria-hidden="true">
        T
      </div>
      {ITEMS.map(({ id, label, icon: Icon, live }) => (
        <button
          key={id}
          type="button"
          className={`${styles.item} ${live ? styles.active : ""}`}
          aria-current={live ? "page" : undefined}
          disabled={!live}
          title={live ? label : `${label} — coming soon`}
        >
          <Icon size={20} aria-hidden="true" />
          <span className={styles.label}>{label}</span>
        </button>
      ))}
    </nav>
  );
}
