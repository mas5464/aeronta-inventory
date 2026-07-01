import { useState } from "react";
import { DemandTrend } from "./DemandTrend";
import type {
  HistoryEntry,
  PartContext,
  PolicyView,
  RecommendationDetail,
  RejectReason,
} from "../api/types";
import { demand, typeLabel } from "../lib/format";
import styles from "./DetailPanel.module.css";

interface Props {
  detail: RecommendationDetail | null;
  onApprove: (id: string) => void;
  onReject: (id: string, reason: RejectReason) => void;
  onDefer: (id: string) => void;
  // Prior writeback ledger for this part/location (newest entry last).
  history?: HistoryEntry[];
  onRollback?: (pn: string, location: string) => void;
  // Stock/lead-time/open-orders/demand context for the selected part/location
  // (Task C3). Optional — renders nothing when absent, so existing callers/tests
  // that don't pass it are unaffected.
  partContext?: PartContext | null;
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

const round = (n: number): number => Math.round(n);

function partHeadline(ctx: PartContext): string {
  const parts = [ctx.attributes.description];
  if (ctx.attributes.part_class) parts.push(ctx.attributes.part_class);
  if (ctx.attributes.ata_chapter) parts.push(`ATA ${ctx.attributes.ata_chapter}`);
  return parts.join(" · ");
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
  partContext,
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

      {partContext && (
        <section className={styles.partContext}>
          <div className={styles.partHead}>{partHeadline(partContext)}</div>
          <p className={styles.partStrip}>
            on hand {partContext.stock ? round(partContext.stock.on_hand) : "—"} · serviceable{" "}
            {partContext.stock ? round(partContext.stock.serviceable) : "—"} · in repair{" "}
            {partContext.stock ? round(partContext.stock.in_repair) : "—"} · need{" "}
            {round(detail.shortage_quantity)} · demand {demand(detail.projected_demand)}/
            {detail.horizon_days}d
          </p>
          {partContext.lead_time && (
            <p className={styles.partStrip}>
              Lead time — promised {partContext.lead_time.promised_days ?? "—"}d · realized{" "}
              {partContext.lead_time.realized_mean_days ?? "—"}d (n=
              {partContext.lead_time.n_observations})
            </p>
          )}
          <p className={styles.partStrip}>
            Open orders — {partContext.open_orders.length} ({round(partContext.total_open_qty)} qty)
          </p>
          <DemandTrend points={partContext.demand?.points ?? []} />
        </section>
      )}

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
