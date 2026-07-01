import { useEffect, useMemo, useState } from "react";
import { HashRouter, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import type { PlannerClient } from "./api/client";
import { ChartRow } from "./components/ChartRow";
import { DashboardView } from "./components/DashboardView";
import { DetailPanel } from "./components/DetailPanel";
import { KillSwitchHeader } from "./components/KillSwitchHeader";
import { NavRail } from "./components/NavRail";
import { QueueTable } from "./components/QueueTable";
import { SummaryCards } from "./components/SummaryCards";
import { QUEUE_PANEL_ID, Tabs, queueTabId } from "./components/Tabs";
import { Toolbar } from "./components/Toolbar";
import { type PlannerTab, usePlanner } from "./hooks/usePlanner";
import {
  type QueueFilter,
  type SortKey,
  type SortSpec,
  queryRows,
  summarize,
  toCsv,
} from "./lib/queryView";
import styles from "./App.module.css";

interface Props {
  client: PlannerClient;
  tenant: string;
}

// The active tab lives in the URL (#/pending, #/decided) so views are deep-linkable.
export function App({ client, tenant }: Props) {
  return (
    <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/dashboard" element={<DashboardView client={client} tenant={tenant} />} />
        <Route path="/:tab" element={<PlannerView client={client} tenant={tenant} />} />
        <Route path="*" element={<Navigate to="/pending" replace />} />
      </Routes>
    </HashRouter>
  );
}

function downloadCsv(name: string, csv: string) {
  if (typeof URL.createObjectURL !== "function") return; // jsdom / unsupported
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function PlannerView({ client, tenant }: Props) {
  const p = usePlanner(client, tenant);
  const navigate = useNavigate();
  const { tab: tabParam } = useParams();
  const urlTab: PlannerTab = tabParam === "decided" ? "decided" : "pending";

  useEffect(() => {
    if (urlTab !== p.tab) p.setTab(urlTab);
  }, [urlTab, p.tab, p.setTab]);

  const [filter, setFilter] = useState<QueueFilter>({});
  const [sort, setSort] = useState<SortSpec>({ key: "priority_score", dir: "desc" });

  const paused = p.killSwitch.engaged;
  const decided = p.tab === "decided";

  const view = useMemo(() => queryRows(p.rows, filter, sort), [p.rows, filter, sort]);
  const summary = useMemo(() => summarize(p.rows), [p.rows]);

  const onSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));

  const onExport = () => downloadCsv(`trax-io-${p.tab}.csv`, toCsv(view));
  const onBulkApprove = () => p.bulkApprove({ tiers: filter.tiers, types: filter.types });

  return (
    <div className={styles.shell}>
      <NavRail active="review" />
      <main className={styles.main}>
        <KillSwitchHeader
          tenant={tenant}
          count={p.rows.length}
          countLabel={decided ? "decided" : "pending"}
          state={p.killSwitch}
          onToggle={p.toggleKill}
        />

        {paused && (
          <div className={styles.killBanner} role="alert">
            Agent paused — approvals are disabled until you resume.
          </div>
        )}
        {p.banner && (
          <div className={styles.banner} role="alert">
            {p.banner}
          </div>
        )}

        <Tabs tab={p.tab} onChange={(t) => navigate(`/${t}`)} />

        <section id={QUEUE_PANEL_ID} role="tabpanel" aria-labelledby={queueTabId(p.tab)} tabIndex={0}>
          {p.loading ? (
            <p className={styles.loading} role="status">
              Loading…
            </p>
          ) : (
            <>
              {!decided && (
                <>
                  <Toolbar
                    filter={filter}
                    onFilterChange={setFilter}
                    onExport={onExport}
                    onBulkApprove={onBulkApprove}
                    bulkDisabled={paused || p.busy}
                  />
                  <SummaryCards summary={summary} />
                  <ChartRow rows={p.rows} />
                </>
              )}
              {decided && (
                <div className={styles.decidedBar}>
                  <button type="button" className={styles.exportOnly} onClick={onExport}>
                    Export
                  </button>
                </div>
              )}
              <QueueTable
                rows={view}
                selectedId={p.selectedId}
                onSelect={p.select}
                onApprove={p.approve}
                disabled={paused}
                busy={p.busy}
                decided={decided}
                sort={sort}
                onSort={onSort}
              />
              <DetailPanel
                detail={p.detail}
                history={p.history}
                partContext={p.partContext}
                onApprove={p.approve}
                onReject={p.reject}
                onDefer={p.defer}
                onRollback={p.rollback}
                approveDisabled={paused}
                busy={p.busy}
                decided={decided}
              />
            </>
          )}
        </section>
      </main>
    </div>
  );
}
