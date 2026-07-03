import type { EvidenceView } from "../api/types";
import { confidenceTier, type ConfidenceTier } from "../lib/confidenceTier";
import { typeLabel } from "../lib/format";
import styles from "./ConfidenceHero.module.css";

interface Props {
  reason: string;
  confidenceScore: number;
  evidence: EvidenceView[];
}

const CONF_CLASS: Record<ConfidenceTier, string> = {
  high: styles.confHigh,
  medium: styles.confMedium,
  low: styles.confLow,
};

export function ConfidenceHero({ reason, confidenceScore, evidence }: Props) {
  const tier = confidenceTier(confidenceScore);
  return (
    <section className={styles.hero}>
      <div className={styles.top}>
        <span className={`${styles.score} ${CONF_CLASS[tier]}`}>
          {Math.round(confidenceScore * 100)}%
        </span>
        <span className={styles.scoreLabel}>confidence</span>
      </div>
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
