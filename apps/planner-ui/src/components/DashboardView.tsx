import { useEffect, useState } from "react";
import type { PlannerClient } from "../api/client";
import type { Breakdown, DashboardSummary } from "../api/types";
import { demand, money } from "../lib/format";
import { NavRail } from "./NavRail";
import styles from "./DashboardView.module.css";
import shellStyles from "../App.module.css";

interface Props {
  client: PlannerClient;
  tenant: string;
}

function BreakdownBars({ title, rows }: { title: string; rows: Breakdown[] }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <div className={styles.breakdownCard}>
      <div className={styles.title}>{title}</div>
      {rows.length === 0 ? (
        <p className={styles.empty}>No data.</p>
      ) : (
        <ul className={styles.bars}>
          {rows.map((r) => (
            <li key={r.key} className={styles.barRow}>
              <span className={styles.barLabel}>{r.key}</span>
              <span className={styles.barTrack}>
                <span className={styles.barFill} style={{ width: `${(r.count / max) * 100}%` }} />
              </span>
              <span className={styles.barCount}>{r.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function round(n: number): number {
  return Math.round(n);
}

export function DashboardView({ client, tenant }: Props) {
  const [dashboard, setDashboard] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDashboard(null);
    setError(null);
    client
      .getDashboard(tenant)
      .then((d) => {
        if (!cancelled) setDashboard(d);
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load the dashboard. Try again later.");
      });
    return () => {
      cancelled = true;
    };
  }, [client, tenant]);

  return (
    <div className={shellStyles.shell}>
      <NavRail active="dashboard" />
      <main className={shellStyles.main}>
        <h1 className={styles.heading}>Portfolio</h1>
        <p className={styles.subheading}>{tenant}</p>

        {error && (
          <div className={shellStyles.killBanner} role="alert">
            {error}
          </div>
        )}

        {!dashboard && !error && (
          <p className={styles.loading} role="status">
            Loading…
          </p>
        )}

        {dashboard && (
          <>
            <div className={styles.grid}>
              {[
                { label: "Parts", value: String(dashboard.parts) },
                { label: "On hand value", value: money(dashboard.total_on_hand_value) },
                { label: "Total on hand", value: String(round(dashboard.total_on_hand)) },
                { label: "Total shortage", value: String(round(dashboard.total_shortage)) },
                { label: "Projected demand", value: demand(dashboard.total_projected_demand) },
                {
                  label: "AOG exposure",
                  value: String(dashboard.aog_exposure),
                  tone: dashboard.aog_exposure > 0 ? "danger" : undefined,
                },
                { label: "Open recommendations", value: String(dashboard.open_recommendations) },
                { label: "Net cost impact", value: money(dashboard.net_cost_impact) },
              ].map((c) => (
                <div key={c.label} className={styles.card}>
                  <div className={styles.label}>{c.label}</div>
                  <div className={`${styles.value} ${c.tone ? styles[c.tone] : ""}`}>{c.value}</div>
                </div>
              ))}
            </div>

            <div className={styles.row}>
              <BreakdownBars title="By criticality" rows={dashboard.by_criticality} />
              <BreakdownBars title="By part class" rows={dashboard.by_part_class} />
            </div>
            <div className={styles.row}>
              <BreakdownBars title="By ATA chapter" rows={dashboard.by_ata} />
              <BreakdownBars title="By tier" rows={dashboard.by_tier} />
            </div>

            <div className={styles.tableCard}>
              <div className={styles.title}>Top shortages</div>
              {dashboard.top_shortages.length === 0 ? (
                <p className={styles.empty}>No shortages.</p>
              ) : (
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>PN · Location</th>
                      <th>On hand</th>
                      <th>Shortage</th>
                      <th>Projected demand</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dashboard.top_shortages.map((s) => (
                      <tr key={`${s.pn}-${s.location}`}>
                        <td>
                          {s.pn} · {s.location}
                        </td>
                        <td>{round(s.on_hand)}</td>
                        <td>{round(s.shortage)}</td>
                        <td>{demand(s.projected_demand)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
