import type { QueueSummary } from "../lib/queryView";
import { money } from "../lib/format";
import styles from "./SummaryCards.module.css";

interface Props {
  summary: QueueSummary;
}

export function SummaryCards({ summary }: Props) {
  const cards: { label: string; value: string; tone?: "danger" | "warning" }[] = [
    { label: "Pending", value: String(summary.count) },
    { label: "Net cost impact", value: money(summary.netCost) },
    {
      label: "AOG risk",
      value: String(summary.aogRisk),
      tone: summary.aogRisk > 0 ? "danger" : undefined,
    },
    {
      label: "Tier A to approve",
      value: String(summary.tierA),
      tone: summary.tierA > 0 ? "warning" : undefined,
    },
  ];
  return (
    <div className={styles.grid}>
      {cards.map((c) => (
        <div key={c.label} className={styles.card}>
          <div className={styles.label}>{c.label}</div>
          <div className={`${styles.value} ${c.tone ? styles[c.tone] : ""}`}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}
