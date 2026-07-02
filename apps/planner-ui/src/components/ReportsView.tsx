import { useEffect, useState } from "react";
import type { PlannerClient } from "../api/client";
import type { BvrReport } from "../api/types";
import { NavRail } from "./NavRail";
import styles from "./ReportsView.module.css";
import shellStyles from "../App.module.css";

interface Props {
  client: PlannerClient;
  tenant: string;
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.tile}>
      <div className={styles.tileValue}>{value}</div>
      <div className={styles.tileLabel}>{label}</div>
    </div>
  );
}

export function ReportsView({ client, tenant }: Props) {
  const [report, setReport] = useState<BvrReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    client
      .getBvr(tenant)
      .then((r) => alive && setReport(r))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [client, tenant]);

  async function handleDownloadPdf(e: React.MouseEvent<HTMLAnchorElement>) {
    e.preventDefault();
    setPdfError(null);
    const url = client.bvrDocumentUrl(tenant, "pdf");
    try {
      const resp = await fetch(url);
      if (resp.ok) {
        window.location.assign(url);
        return;
      }
      let detail = `HTTP ${resp.status}`;
      try {
        const body = (await resp.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        // non-JSON error body; keep the status message
      }
      setPdfError(`PDF unavailable: ${detail}`);
    } catch (err: unknown) {
      setPdfError(`PDF unavailable: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  return (
    <div className={shellStyles.shell}>
      <NavRail active="reports" />
      <main className={styles.main}>
        <header className={styles.header}>
          <h1>Business Value Report</h1>
          {report && (
            <span className={styles.badge} title="Projected against the pre-agent baseline">
              All figures projected
            </span>
          )}
        </header>
        {error && <p role="alert">Couldn&apos;t load the report: {error}</p>}
        {!report && !error && <p role="status">Loading report…</p>}
        {report && (
          <>
            <p className={styles.meta}>
              {report.period.label} · schema {report.schema_version} ·{" "}
              {report.executive_summary.service_headline}
            </p>
            <section className={styles.tiles} aria-label="Executive summary">
              <Tile label="Total projected" value={`$${report.executive_summary.total_projected}`} />
              <Tile label="Changes applied" value={String(report.executive_summary.changes_applied)} />
              <Tile label="Changes shadowed" value={String(report.executive_summary.changes_shadowed)} />
              <Tile
                label="Keys under management"
                value={report.executive_summary.keys_under_management.toLocaleString()}
              />
              <Tile label="Open pipeline" value={`$${report.executive_summary.open_pipeline_value}`} />
            </section>
            <section aria-label="Savings decomposition" className={styles.section}>
              <h2>Savings (projected)</h2>
              <p className={styles.meta}>
                {report.savings.changes_valued} of {report.savings.changes_total} changes valued ·
                applied ${report.savings.total_projected_applied} · shadowed $
                {report.savings.total_projected_shadowed}
              </p>
              <ul className={styles.components}>
                {[
                  report.savings.holding_cost_delta,
                  report.savings.ordering_cost_delta,
                  report.savings.stockout_risk_delta,
                ].map((c) => (
                  <li key={c.name}>
                    <span className={styles.componentName}>{c.name}</span>
                    <span className={styles.componentAmount}>${c.amount}</span>
                  </li>
                ))}
              </ul>
            </section>
            <section aria-label="Governance" className={styles.section}>
              <h2>Governance</h2>
              <p>
                {report.governance.recommendations_total} recommendations · approval rate{" "}
                {(report.governance.approval_rate * 100).toFixed(1)}% · override rate{" "}
                {(report.governance.override_rate * 100).toFixed(1)}% ·{" "}
                {report.governance.rollbacks} rollbacks
              </p>
            </section>
            <p className={styles.links}>
              <a
                href={client.bvrDocumentUrl(tenant, "html")}
                target="_blank"
                rel="noreferrer"
              >
                Open printable report
              </a>
              <a href={client.bvrDocumentUrl(tenant, "pdf")} onClick={handleDownloadPdf}>
                Download PDF
              </a>
            </p>
            {pdfError && <p role="alert">{pdfError}</p>}
          </>
        )}
      </main>
    </div>
  );
}
