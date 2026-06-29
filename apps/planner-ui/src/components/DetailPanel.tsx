import { useState } from "react";
import type { HistoryEntry, PolicyView, RecommendationDetail, RejectReason } from "../api/types";
import { typeLabel } from "../lib/format";
import styles from "./DetailPanel.module.css";

interface Props {
  detail: RecommendationDetail | null;
  onApprove: (id: string) => void;
  onReject: (id: string, reason: RejectReason) => void;
  onDefer: (id: string) => void;
  // Prior writeback ledger for this part/location (newest entry last).
  history?: HistoryEntry[];
  onRollback?: (pn: string, location: string) => void;
  // Only approve writes to eMRO, so only approve is blocked by the kill switch.
  // Reject and defer never write, so they stay enabled when the agent is paused.
  approveDisabled?: boolean;
  // A write is in flight — disable every action until it settles (double-submit guard).
  busy?: boolean;
  // Decided view: the recommendation is already resolved — hide approve/reject/defer
  // (only the writeback history + rollback remain relevant).
  decided?: boolean;
}

function valueSummary(values: Record<string, number>): string {
  return `ROP ${values.rop} · EOQ ${values.eoq} · SS ${values.safety_stock} · Max ${values.max_stock}`;
}

function changedOn(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 10);
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

export function DetailPanel({
  detail,
  onApprove,
  onReject,
  onDefer,
  history = [],
  onRollback,
  approveDisabled,
  busy,
  decided,
}: Props) {
  const [reason, setReason] = useState<RejectReason>("wrong_for_fleet");

  if (detail === null) {
    return (
      <div className={styles.empty}>Select a recommendation to review its provenance.</div>
    );
  }

  const id = detail.recommendation_id;
  const advisory = detail.proposed_policy === null;
  const approveBlocked = approveDisabled || advisory || busy;

  // The most recent applied write is revertible only if a prior value is known.
  const latestWrite = [...history].reverse().find((e) => e.status === "written");
  const revertible = latestWrite != null && latestWrite.old_values !== null;

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

      <section className={styles.history}>
        <div className={styles.historyHead}>
          <span className={styles.label}>Writeback history</span>
          {onRollback && (
            <button
              type="button"
              className={styles.rollback}
              disabled={busy || !revertible}
              title={
                revertible
                  ? undefined
                  : "Nothing to roll back — no prior agent-applied value is on record"
              }
              onClick={() => onRollback(detail.pn, detail.location)}
            >
              Roll back last change
            </button>
          )}
        </div>
        {history.length === 0 ? (
          <p className={styles.historyEmpty}>
            No prior writes for {detail.pn} · {detail.location}.
          </p>
        ) : (
          <ol className={styles.timeline}>
            {[...history].reverse().map((e) => (
              <li key={e.version} className={styles.histRow}>
                <span className={styles.histVer}>v{e.version}</span>
                <span className={styles.histStatus} data-status={e.status}>
                  {typeLabel(e.status)}
                </span>
                <span className={styles.histVals}>{valueSummary(e.new_values)}</span>
                <span className={styles.histMeta}>
                  {changedOn(e.changed_at)} · {e.changed_by_principal}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {!decided && (
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
          <button type="button" disabled={busy} onClick={() => onDefer(id)}>
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
            <button type="button" disabled={busy} onClick={() => onReject(id, reason)}>
              Reject
            </button>
          </span>
        </div>
      )}
    </div>
  );
}
