import { useEffect } from "react";
import { HashRouter, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import type { PlannerClient } from "./api/client";
import { BulkApproveBar } from "./components/BulkApproveBar";
import { DetailPanel } from "./components/DetailPanel";
import { KillSwitchHeader } from "./components/KillSwitchHeader";
import { QueueTable } from "./components/QueueTable";
import { QUEUE_PANEL_ID, Tabs, queueTabId } from "./components/Tabs";
import { type PlannerTab, usePlanner } from "./hooks/usePlanner";
import styles from "./App.module.css";

interface Props {
  client: PlannerClient;
  tenant: string;
}

// The active tab lives in the URL (#/pending, #/decided) so views are deep-linkable.
// HashRouter avoids server rewrites — the UI is embedded as an eMRO module.
export function App({ client, tenant }: Props) {
  return (
    <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/:tab" element={<PlannerView client={client} tenant={tenant} />} />
        <Route path="*" element={<Navigate to="/pending" replace />} />
      </Routes>
    </HashRouter>
  );
}

function PlannerView({ client, tenant }: Props) {
  const p = usePlanner(client, tenant);
  const navigate = useNavigate();
  const { tab: tabParam } = useParams();
  const urlTab: PlannerTab = tabParam === "decided" ? "decided" : "pending";

  // URL is the source of truth for the active tab — sync it into the hook.
  useEffect(() => {
    if (urlTab !== p.tab) p.setTab(urlTab);
  }, [urlTab, p.tab, p.setTab]);

  const paused = p.killSwitch.engaged;
  const decided = p.tab === "decided";

  return (
    <main className={styles.app}>
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
            {!decided && p.rows.length > 0 && (
              <BulkApproveBar onBulkApprove={p.bulkApprove} disabled={paused || p.busy} />
            )}
            <QueueTable
              rows={p.rows}
              selectedId={p.selectedId}
              onSelect={p.select}
              onApprove={p.approve}
              disabled={paused}
              busy={p.busy}
              decided={decided}
            />
            <DetailPanel
              detail={p.detail}
              history={p.history}
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
  );
}
