import { useCallback, useEffect, useRef, useState } from "react";
import { PlannerError, type PlannerClient } from "../api/client";
import type {
  BulkApproveFilter,
  HistoryEntry,
  KillSwitchState,
  QueueRow,
  RecommendationDetail,
  RejectReason,
  RollbackRequest,
  TaskStatus,
} from "../api/types";

// The queue is viewed one tab at a time: "pending" (the approval queue) or "decided"
// (approved/rejected/deferred, merged). Decided rows are read-only except writeback rollback.
export type PlannerTab = "pending" | "decided";
const DECIDED_STATUSES: TaskStatus[] = ["approved", "rejected", "deferred"];

// A PlannerError carries the BFF's `detail` (e.g. "kill switch engaged"); anything else
// (network/parse failure) gets a generic message so the user always sees feedback.
function messageFor(err: unknown): string {
  return err instanceof PlannerError ? err.message : "Something went wrong. Please try again.";
}

export interface PlannerState {
  rows: QueueRow[];
  selectedId: string | null;
  detail: RecommendationDetail | null;
  history: HistoryEntry[];
  killSwitch: KillSwitchState;
  loading: boolean;
  // True while an approve/reject/defer write is in flight — gates double-submits.
  busy: boolean;
  banner: string | null;
  tab: PlannerTab;
  setTab: (tab: PlannerTab) => void;
  select: (id: string) => void;
  approve: (id: string) => void;
  reject: (id: string, reason: RejectReason) => void;
  defer: (id: string) => void;
  bulkApprove: (filter: BulkApproveFilter) => void;
  rollback: (pn: string, location: string) => void;
  toggleKill: (engaged: boolean) => void;
}

export function usePlanner(client: PlannerClient, tenant: string): PlannerState {
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RecommendationDetail | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [killSwitch, setKillSwitch] = useState<KillSwitchState>({ engaged: false });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [tab, setTabState] = useState<PlannerTab>("pending");

  // Monotonic token so a slow getDetail for a stale selection can't overwrite a newer one.
  const selectSeq = useRef(0);
  // Synchronous in-flight latch — state updates are async, so a ref guards back-to-back clicks.
  const inFlight = useRef(false);

  const reload = useCallback(async () => {
    // "decided" merges approved/rejected/deferred (the BFF queue filters one status at a time).
    const queue =
      tab === "pending"
        ? client.getQueue(tenant, "pending")
        : Promise.all(DECIDED_STATUSES.map((s) => client.getQueue(tenant, s))).then((lists) =>
            lists.flat().sort((a, b) => b.priority_score - a.priority_score),
          );
    const [q, ks] = await Promise.all([queue, client.getKillSwitch(tenant)]);
    setRows(q);
    setKillSwitch(ks);
  }, [client, tenant, tab]);

  // Switching tab reloads (reload depends on `tab`) and drops the now-stale selection.
  const setTab = useCallback((next: PlannerTab) => {
    setTabState(next);
    setSelectedId(null);
    setDetail(null);
    setHistory([]);
    setBanner(null);
    selectSeq.current++;
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    reload().finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [reload]);

  const select = useCallback(
    (id: string) => {
      setSelectedId(id);
      setHistory([]);
      const seq = ++selectSeq.current;
      client
        .getDetail(tenant, id)
        .then((d) => {
          // Drop the response if a newer selection has since been made.
          if (seq !== selectSeq.current) return;
          setDetail(d);
          // Pull this part/location's writeback history alongside the detail.
          return client.getHistory(tenant, d.pn, d.location).then((h) => {
            if (seq === selectSeq.current) setHistory(h);
          });
        })
        .catch((err) => {
          if (seq === selectSeq.current) setBanner(messageFor(err));
        });
    },
    [client, tenant],
  );

  // approve/reject/defer/bulk-approve mutate the queue, so the acted rows leave it — refresh
  // and clear the (now-stale) selection. A PlannerError (e.g. 423 kill switch) becomes a banner;
  // onDone runs after a successful refresh (e.g. to report a bulk count).
  const runWrite = useCallback(
    async <T,>(fn: () => Promise<T>, onDone?: (result: T) => void) => {
      if (inFlight.current) return; // double-submit guard
      inFlight.current = true;
      setBusy(true);
      setBanner(null);
      try {
        const result = await fn();
        await reload();
        setSelectedId(null);
        setDetail(null);
        setHistory([]);
        selectSeq.current++; // invalidate any detail still loading for the cleared selection
        onDone?.(result);
      } catch (err) {
        setBanner(messageFor(err));
      } finally {
        inFlight.current = false;
        setBusy(false);
      }
    },
    [reload],
  );

  const approve = useCallback(
    (id: string) => void runWrite(() => client.approve(tenant, id)),
    [runWrite, client, tenant],
  );
  const reject = useCallback(
    (id: string, reason: RejectReason) => void runWrite(() => client.reject(tenant, id, reason)),
    [runWrite, client, tenant],
  );
  const defer = useCallback(
    (id: string) => void runWrite(() => client.defer(tenant, id)),
    [runWrite, client, tenant],
  );
  const bulkApprove = useCallback(
    (filter: BulkApproveFilter) =>
      void runWrite(
        () => client.bulkApprove(tenant, filter),
        (res) => {
          const n = res.approved_count;
          setBanner(`Approved ${n} recommendation${n === 1 ? "" : "s"}.`);
        },
      ),
    [runWrite, client, tenant],
  );

  // Rollback acts on the writeback ledger, not the queue — so it refreshes history in place
  // and leaves the current selection intact (unlike approve/reject/defer).
  const rollback = useCallback(
    (pn: string, location: string) => {
      if (inFlight.current) return;
      inFlight.current = true;
      setBusy(true);
      setBanner(null);
      const req: RollbackRequest = {
        tenant_id: tenant,
        pn,
        location,
        reason: "planner rollback",
        principal: "planner",
        requested_at: new Date().toISOString(),
      };
      client
        .rollback(tenant, req)
        .then(async (res) => {
          if (res.status === "rolled_back") {
            setHistory(await client.getHistory(tenant, pn, location));
            setBanner(`Rolled back ${pn} · ${location} to the previous policy.`);
          } else {
            setBanner(`Rollback not applied: ${res.status.replace(/_/g, " ")}.`);
          }
        })
        .catch((err) => setBanner(messageFor(err)))
        .finally(() => {
          inFlight.current = false;
          setBusy(false);
        });
    },
    [client, tenant],
  );

  const toggleKill = useCallback(
    (engaged: boolean) => {
      setBanner(null);
      client
        .setKillSwitch(tenant, engaged)
        .then(setKillSwitch)
        .catch((err) => setBanner(messageFor(err)));
    },
    [client, tenant],
  );

  return {
    rows, selectedId, detail, history, killSwitch, loading, busy, banner, tab,
    setTab, select, approve, reject, defer, bulkApprove, rollback, toggleKill,
  };
}
