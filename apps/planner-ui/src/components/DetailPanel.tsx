import { useState } from "react";
import type { PolicyView, RecommendationDetail, RejectReason } from "../api/types";
import { typeLabel } from "../lib/format";
import styles from "./DetailPanel.module.css";

interface Props {
  detail: RecommendationDetail | null;
  onApprove: (id: string) => void;
  onReject: (id: string, reason: RejectReason) => void;
  onDefer: (id: string) => void;
  // Only approve writes to eMRO, so only approve is blocked by the kill switch.
  // Reject and defer never write, so they stay enabled when the agent is paused.
  approveDisabled?: boolean;
}

const REASONS: { value: RejectReason; label: string }[] = [
  { value: "wrong_for_fleet", label: "Wrong for fleet" },
  { value: "wrong_essentiality", label: "Wrong essentiality" },
  { value: "bad_lead_time", label: "Bad lead time" },
  { value: "planner_override", label: "Planner override" },
  { value: "other", label: "Other" },
];

const POLICY_FIELDS: { key: keyof PolicyView; label: string }[] = [
  { key: "rop", label: "ROP" },
  { key: "eoq", label: "EOQ" },
  { key: "safety_stock", label: "Safety stock" },
  { key: "max_stock", label: "Max" },
];

export function DetailPanel({ detail, onApprove, onReject, onDefer, approveDisabled }: Props) {
  const [reason, setReason] = useState<RejectReason>("wrong_for_fleet");

  if (detail === null) {
    return (
      <div className={styles.empty}>Select a recommendation to review its provenance.</div>
    );
  }

  const id = detail.recommendation_id;
  const advisory = detail.proposed_policy === null;
  const approveBlocked = approveDisabled || advisory;

  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <div>
          <span className={styles.pn}>
            {detail.pn} · {detail.location}
          </span>
          <span className={styles.meta}>
            {" "}
            · {typeLabel(detail.type)} · confidence {detail.confidence_score.toFixed(2)}
          </span>
        </div>
        {detail.provenance_id && <span className={styles.prov}>{detail.provenance_id}</span>}
      </div>

      <div className={styles.cols}>
        <section>
          <div className={styles.label}>Current → proposed</div>
          {advisory ? (
            <p className={styles.advisory}>Advisory — no writable policy change.</p>
          ) : (
            <table className={styles.policy}>
              <tbody>
                {POLICY_FIELDS.map(({ key, label }) => (
                  <tr key={key}>
                    <td className={styles.field}>{label}</td>
                    <td className={styles.diff}>
                      {detail.current_policy ? detail.current_policy[key] : "—"}{" "}
                      <span aria-hidden="true">→</span>{" "}
                      <span className={styles.proposed}>{detail.proposed_policy![key]}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section>
          <div className={styles.label}>Why this is queued</div>
          <p className={styles.reason}>{detail.reason}</p>
          {detail.supporting_evidence.length > 0 && (
            <>
              <div className={styles.label}>Evidence</div>
              <ul className={styles.evidence}>
                {detail.supporting_evidence.map((e) => (
                  <li key={e.ref_id}>
                    <span className={styles.evKind}>{typeLabel(e.kind)}</span> {e.detail}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.approve}
          disabled={approveBlocked}
          title={advisory ? "Advisory recommendation — nothing to write" : undefined}
          onClick={() => onApprove(id)}
        >
          Approve
        </button>
        <button type="button" onClick={() => onDefer(id)}>
          Defer
        </button>
        <span className={styles.rejectGroup}>
          <select
            aria-label="Rejection reason"
            value={reason}
            onChange={(e) => setReason(e.target.value as RejectReason)}
          >
            {REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => onReject(id, reason)}>
            Reject
          </button>
        </span>
      </div>
    </div>
  );
}
