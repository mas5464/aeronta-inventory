import { useCallback, useEffect, useState } from "react";
import { PlannerError, type PlannerClient } from "../api/client";
import type { KillSwitchState, QueueRow, RecommendationDetail, RejectReason } from "../api/types";

export interface PlannerState {
  rows: QueueRow[];
  selectedId: string | null;
  detail: RecommendationDetail | null;
  killSwitch: KillSwitchState;
  loading: boolean;
  banner: string | null;
  select: (id: string) => void;
  approve: (id: string) => void;
  reject: (id: string, reason: RejectReason) => void;
  defer: (id: string) => void;
  toggleKill: (engaged: boolean) => void;
}

export function usePlanner(client: PlannerClient, tenant: string): PlannerState {
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RecommendationDetail | null>(null);
  const [killSwitch, setKillSwitch] = useState<KillSwitchState>({ engaged: false });
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const [q, ks] = await Promise.all([client.getQueue(tenant), client.getKillSwitch(tenant)]);
    setRows(q);
    setKillSwitch(ks);
  }, [client, tenant]);

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
      client
        .getDetail(tenant, id)
        .then(setDetail)
        .catch((err) => err instanceof PlannerError && setBanner(err.message));
    },
    [client, tenant],
  );

  // approve/reject/defer mutate the queue, so the acted row leaves it — refresh and
  // clear the (now-stale) selection. A PlannerError (e.g. 423 kill switch) becomes a banner.
  const act = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBanner(null);
      try {
        await fn();
        await reload();
        setSelectedId(null);
        setDetail(null);
      } catch (err) {
        if (err instanceof PlannerError) setBanner(err.message);
        else throw err;
      }
    },
    [reload],
  );

  const approve = useCallback((id: string) => void act(() => client.approve(tenant, id)), [act, client, tenant]);
  const reject = useCallback(
    (id: string, reason: RejectReason) => void act(() => client.reject(tenant, id, reason)),
    [act, client, tenant],
  );
  const defer = useCallback((id: string) => void act(() => client.defer(tenant, id)), [act, client, tenant]);

  const toggleKill = useCallback(
    (engaged: boolean) => {
      setBanner(null);
      client
        .setKillSwitch(tenant, engaged)
        .then(setKillSwitch)
        .catch((err) => err instanceof PlannerError && setBanner(err.message));
    },
    [client, tenant],
  );

  return {
    rows, selectedId, detail, killSwitch, loading, banner,
    select, approve, reject, defer, toggleKill,
  };
}
