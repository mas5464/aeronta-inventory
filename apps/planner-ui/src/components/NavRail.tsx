import { ClipboardCheck, FileText, History, LayoutDashboard, Moon, Settings, Sun } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTheme } from "../hooks/useTheme";
import styles from "./NavRail.module.css";

export type NavSection = "review" | "dashboard" | "reports";

// App-shell navigation. "Review", "Dashboard", and "Reports" are the live sections;
// the others are placeholders for future sections, shown disabled so the shell reads
// as a system.
const ITEMS: { id: NavSection | "writebacks" | "settings"; label: string; icon: typeof ClipboardCheck; live?: boolean; href?: string }[] = [
  { id: "review", label: "Review", icon: ClipboardCheck, live: true, href: "#/pending" },
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, live: true, href: "#/dashboard" },
  { id: "reports", label: "Reports", icon: FileText, live: true, href: "#/reports" },
  { id: "writebacks", label: "Writebacks", icon: History },
  { id: "settings", label: "Settings", icon: Settings },
];

interface Props {
  active?: NavSection;
}

export function NavRail({ active = "review" }: Props) {
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();
  return (
    <nav className={styles.rail} aria-label="Sections">
      <div className={styles.brand} aria-hidden="true">
        T
      </div>
      {ITEMS.map(({ id, label, icon: Icon, live, href }) => {
        const current = live && id === active;
        return (
          <button
            key={id}
            type="button"
            className={`${styles.item} ${current ? styles.active : ""}`}
            aria-current={current ? "page" : undefined}
            disabled={!live}
            title={live ? label : `${label} — coming soon`}
            onClick={live && href ? () => navigate(href.replace(/^#/, "")) : undefined}
          >
            <Icon size={20} aria-hidden="true" />
            <span className={styles.label}>{label}</span>
          </button>
        );
      })}
      <button
        type="button"
        className={styles.themeToggle}
        onClick={toggleTheme}
        title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      >
        {theme === "dark" ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
      </button>
    </nav>
  );
}
