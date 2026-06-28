import type { PlannerClient } from "./api/client";
import { BulkApproveBar } from "./components/BulkApproveBar";
import { DetailPanel } from "./components/DetailPanel";
import { KillSwitchHeader } from "./components/KillSwitchHeader";
import { QueueTable } from "./components/QueueTable";
import { usePlanner } from "./hooks/usePlanner";
import styles from "./App.module.css";

interface Props {
  client: PlannerClient;
  tenant: string;
}

export function App({ client, tenant }: Props) {
  const p = usePlanner(client, tenant);
  const paused = p.killSwitch.engaged;

  return (
    <main className={styles.app}>
      <KillSwitchHeader
        tenant={tenant}
        pending={p.rows.length}
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

      {p.loading ? (
        <p className={styles.loading}>Loading the queue…</p>
      ) : (
        <>
          {p.rows.length > 0 && (
            <BulkApproveBar onBulkApprove={p.bulkApprove} disabled={paused || p.busy} />
          )}
          <QueueTable
            rows={p.rows}
            selectedId={p.selectedId}
            onSelect={p.select}
            onApprove={p.approve}
            disabled={paused}
            busy={p.busy}
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
          />
        </>
      )}
    </main>
  );
}
