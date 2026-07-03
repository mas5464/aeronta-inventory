import { useEffect, useMemo, useState } from "react";
import { HashRouter, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import type { PlannerClient } from "./api/client";
import type { ActionResult } from "./api/types";
import { ChartRow } from "./components/ChartRow";
import { DashboardView } from "./components/DashboardView";
import { DetailPanel } from "./components/DetailPanel";
import { Drawer } from "./components/Drawer";
import { KillSwitchHeader } from "./components/KillSwitchHeader";
import { NavRail } from "./components/NavRail";
import { Pager } from "./components/Pager";
import { QueueTable } from "./components/QueueTable";
import { ReportsView } from "./components/ReportsView";
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
        <Route path="/reports" element={<ReportsView client={client} tenant={tenant} />} />
        <Route path="/:tab/:id" element={<PlannerView client={client} tenant={tenant} />} />
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

function allSameOutcome(results: ActionResult[]): boolean {
  return results.every((r) => r.writeback?.status === results[0].writeback?.status);
}

function PlannerView({ client, tenant }: Props) {
  const p = usePlanner(client, tenant);
  const navigate = useNavigate();
  const { tab: tabParam, id: idParam } = useParams();
  const urlTab: PlannerTab = tabParam === "decided" ? "decided" : "pending";

  useEffect(() => {
    if (urlTab !== p.tab) p.setTab(urlTab);
  }, [urlTab, p.tab, p.setTab]);

  useEffect(() => {
    if (idParam) {
      if (idParam !== p.selectedId) p.select(idParam);
    } else if (p.selectedId) {
      p.deselect();
    }
  }, [idParam, p.selectedId, p.select, p.deselect]);

  const [filter, setFilter] = useState<QueueFilter>({});
  const [sort, setSort] = useState<SortSpec>({ key: "priority_score", dir: "desc" });

  const paused = p.killSwitch.engaged;
  const decided = p.tab === "decided";

  // p.rows is only the current server page (queue is paginated — see usePlanner), so
  // this search/filter/sort is scoped to the loaded page, not the full queue. That's a
  // documented Wave-3 limitation: cross-page search/sort would need server-side support.
  const view = useMemo(() => queryRows(p.rows, filter, sort), [p.rows, filter, sort]);
  const summary = useMemo(() => summarize(p.rows), [p.rows]);

  const onSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));

  const onExport = () => downloadCsv(`trax-io-${p.tab}.csv`, toCsv(view));
  const onBulkApprove = () => p.bulkApprove({ tiers: filter.tiers, types: filter.types });
  const onSelectRow = (id: string) => navigate(id === p.selectedId ? `/${p.tab}` : `/${p.tab}/${id}`);
  const onCloseDrawer = () => navigate(`/${p.tab}`);

  return (
    <div className={styles.shell}>
      <NavRail active="review" />
      <main className={styles.main}>
        <KillSwitchHeader
          tenant={tenant}
          count={p.total}
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
            {p.bulkResults && !allSameOutcome(p.bulkResults) && (
              <details className={styles.bulkDetails}>
                <summary>See per-item results ({p.bulkResults.length})</summary>
                <ul className={styles.bulkList}>
                  {p.bulkResults.map((r) => (
                    <li key={r.recommendation_id}>
                      {r.writeback ? `${r.writeback.pn} · ${r.writeback.location}` : r.recommendation_id}
                      {" — "}
                      {r.message}
                    </li>
                  ))}
                </ul>
              </details>
            )}
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
                onSelect={onSelectRow}
                onApprove={p.approve}
                disabled={paused}
                busy={p.busy}
                decided={decided}
                sort={sort}
                onSort={onSort}
              />
              {!decided && (
                <Pager page={p.page} limit={p.limit} total={p.total} onPrev={p.prevPage} onNext={p.nextPage} />
              )}
              <Drawer open={p.selectedId != null} onClose={onCloseDrawer}>
                {p.selectedId && !p.detail ? (
                  <p className={styles.loading} role="status">
                    Loading…
                  </p>
                ) : (
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
                )}
              </Drawer>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
