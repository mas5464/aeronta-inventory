import { Sparkles } from "lucide-react";
import type { EvidenceView, TaskStatus } from "../api/types";
import { confidenceTier, type ConfidenceTier } from "../lib/confidenceTier";
import { typeLabel } from "../lib/format";
import styles from "./ConfidenceHero.module.css";

interface Props {
  reason: string;
  confidenceScore: number;
  evidence: EvidenceView[];
  status: TaskStatus;
}

const CONF_CLASS: Record<ConfidenceTier, string> = {
  high: styles.confHigh,
  medium: styles.confMedium,
  low: styles.confLow,
};

const STATUS_CLASS: Record<Exclude<TaskStatus, "pending">, string> = {
  approved: styles.status_approved,
  rejected: styles.status_rejected,
  deferred: styles.status_deferred,
};

export function ConfidenceHero({ reason, confidenceScore, evidence, status }: Props) {
  const tier = confidenceTier(confidenceScore);
  return (
    <section className={styles.hero}>
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.iconTile}>
            <Sparkles size={16} aria-hidden="true" />
          </span>
          <div>
            <div className={styles.title}>AI Recommendation</div>
            <div className={styles.subtitle}>Powered by predictive analytics</div>
          </div>
        </div>
        {status !== "pending" && (
          <span className={`${styles.status} ${STATUS_CLASS[status]}`}>{status}</span>
        )}
      </div>
      <div className={styles.top}>
        <span className={`${styles.score} ${CONF_CLASS[tier]}`}>
          {Math.round(confidenceScore * 100)}%
        </span>
        <span className={styles.scoreLabel}>confidence score</span>
      </div>
      <div className={styles.reasonHeading}>Why this recommendation?</div>
      <p className={styles.reason}>{reason}</p>
      {evidence.length > 0 && (
        <>
          <div className={styles.label}>Key findings</div>
          <ul className={styles.evidence}>
            {evidence.map((e) => (
              <li key={e.ref_id}>
                <span className={styles.evKind}>{typeLabel(e.kind)}</span> {e.detail}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
