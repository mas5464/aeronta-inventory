import { useCallback, useEffect, useRef, useState } from "react";
import { PlannerError, type PlannerClient } from "../api/client";
import type {
  BulkApproveFilter,
  HistoryEntry,
  KillSwitchState,
  PartContext,
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

const DEFAULT_LIMIT = 50;
// Decided merges 3 statuses client-side, so each status is fetched with a high
// limit rather than paged individually (Wave-3 limitation — see reload()).
const DECIDED_FETCH_LIMIT = 200;

// A PlannerError carries the BFF's `detail` (e.g. "kill switch engaged"); anything else
// (network/parse failure) gets a generic message so the user always sees feedback.
function messageFor(err: unknown): string {
  return err instanceof PlannerError ? err.message : "Something went wrong. Please try again.";
}

export interface PlannerState {
  rows: QueueRow[];
  total: number;
  page: number;
  limit: number;
  nextPage: () => void;
  prevPage: () => void;
  selectedId: string | null;
  detail: RecommendationDetail | null;
  history: HistoryEntry[];
  partContext: PartContext | null;
  killSwitch: KillSwitchState;
  loading: boolean;
  // True while an approve/reject/defer write is in flight — gates double-submits.
  busy: boolean;
  banner: string | null;
  tab: PlannerTab;
  setTab: (tab: PlannerTab) => void;
  select: (id: string) => void;
  deselect: () => void;
  approve: (id: string) => void;
  reject: (id: string, reason: RejectReason) => void;
  defer: (id: string) => void;
  bulkApprove: (filter: BulkApproveFilter) => void;
  rollback: (pn: string, location: string) => void;
  toggleKill: (engaged: boolean) => void;
}

export function usePlanner(
  client: PlannerClient,
  tenant: string,
  limit: number = DEFAULT_LIMIT,
): PlannerState {
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RecommendationDetail | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [partContext, setPartContext] = useState<PartContext | null>(null);
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
    // Each status is fetched with a high limit and merged/sorted client-side rather than
    // paged server-side — acceptable for now (documented Wave-3 limitation); the Decided
    // tab has no pager control.
    const queue =
      tab === "pending"
        ? client.getQueue(tenant, "pending", limit, page * limit)
        : Promise.all(
            DECIDED_STATUSES.map((s) => client.getQueue(tenant, s, DECIDED_FETCH_LIMIT, 0)),
          ).then((pages) => {
            const items = pages.flatMap((p) => p.items).sort((a, b) => b.priority_score - a.priority_score);
            return { items, total: items.length, limit: DECIDED_FETCH_LIMIT, offset: 0 };
          });
    const [q, ks] = await Promise.all([queue, client.getKillSwitch(tenant)]);
    setRows(q.items);
    setTotal(q.total);
    setKillSwitch(ks);
  }, [client, tenant, tab, page, limit]);

  // Switching tab reloads (reload depends on `tab`) and drops the now-stale selection.
  const setTab = useCallback((next: PlannerTab) => {
    setTabState(next);
    setPage(0);
    setSelectedId(null);
    setDetail(null);
    setHistory([]);
    setPartContext(null);
    setBanner(null);
    selectSeq.current++;
  }, []);

  const nextPage = useCallback(() => {
    setPage((p) => (p + 1) * limit < total ? p + 1 : p);
  }, [limit, total]);

  const prevPage = useCallback(() => {
    setPage((p) => Math.max(0, p - 1));
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
      setPartContext(null);
      const seq = ++selectSeq.current;

      const fetchPartContext = (pn: string, location: string) => {
        client
          .getPartContext(tenant, pn, location)
          .then((pc) => {
            if (seq === selectSeq.current) setPartContext(pc);
          })
          .catch((err) => {
            // Part context is supplementary — a failure here shouldn't clobber the
            // detail/history banner or block the rest of the selection flow.
            console.error("Failed to load part context", err);
          });
      };

      // Fast path: the row is already on the loaded page, so part context can load
      // in parallel with getDetail/getHistory below. Deep-links (or a row on a
      // different page) fall back to the pn/location on the resolved detail instead.
      const row = rows.find((r) => r.recommendation_id === id);
      if (row) fetchPartContext(row.pn, row.location);

      client
        .getDetail(tenant, id)
        .then((d) => {
          // Drop the response if a newer selection has since been made.
          if (seq !== selectSeq.current) return;
          setDetail(d);
          if (!row) fetchPartContext(d.pn, d.location);
          // Pull this part/location's writeback history alongside the detail.
          return client.getHistory(tenant, d.pn, d.location).then((h) => {
            if (seq === selectSeq.current) setHistory(h);
          });
        })
        .catch((err) => {
          if (seq === selectSeq.current) setBanner(messageFor(err));
        });
    },
    [client, tenant, rows],
  );

  const deselect = useCallback(() => {
    setSelectedId(null);
    setDetail(null);
    setHistory([]);
    setPartContext(null);
    selectSeq.current++; // invalidate any in-flight fetch tied to the old selection
  }, []);

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
        setPartContext(null);
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
    rows, total, page, limit, nextPage, prevPage,
    selectedId, detail, history, partContext, killSwitch, loading, busy, banner, tab,
    setTab, select, deselect, approve, reject, defer, bulkApprove, rollback, toggleKill,
  };
}
