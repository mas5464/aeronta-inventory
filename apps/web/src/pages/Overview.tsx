import { useRef, useState, type ReactNode, type RefObject } from "react";
import { Metric } from "@/components/Metric";
import { DrillableCard } from "@/components/drill/DrillableCard";
import { DrillPanel } from "@/components/drill/DrillPanel";
import { HealthMixDonut } from "@/components/HealthMixDonut";
import { AtaRiskList } from "@/components/AtaRiskList";
import { PriorityActionsPreview } from "@/components/PriorityActionsPreview";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { SlInvestmentPanel } from "@/components/SlInvestmentPanel";
import { DrillContent } from "@/features/overview/DrillContent";
import { criticalityLabel, DRILL_SPECS, KPI_DRILL_MAP } from "@/features/overview/drillSpecs";
import { useDashboard } from "@/lib/api/useDashboard";
import { dashboardProvenance } from "@/lib/dashboardProvenance";
import { withProvenance, type Provenance } from "@/lib/provenance";
import type { DashboardSummary } from "@/lib/api/types";

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const integerFormatter = new Intl.NumberFormat("en-US");

interface KpiDef {
  /** Also the `KPI_DRILL_MAP` lookup key and this card's drill-state id. */
  key: string;
  title: string;
  metric: (data: DashboardSummary) => number;
  format: (value: number) => string;
}

const KPI_DEFS: readonly KpiDef[] = [
  { key: "parts", title: "Parts", metric: (d) => d.parts, format: integerFormatter.format },
  {
    key: "total_on_hand",
    title: "Total on-hand",
    metric: (d) => d.total_on_hand,
    format: integerFormatter.format,
  },
  {
    key: "on_hand_value",
    title: "On-hand value",
    metric: (d) => d.total_on_hand_value,
    format: currencyFormatter.format,
  },
  {
    key: "total_shortage",
    title: "Total shortage",
    metric: (d) => d.total_shortage,
    format: integerFormatter.format,
  },
  {
    key: "projected_demand",
    title: "Projected demand",
    metric: (d) => d.total_projected_demand,
    format: integerFormatter.format,
  },
  {
    key: "aog_exposure",
    title: "AOG exposure",
    metric: (d) => d.aog_exposure,
    format: integerFormatter.format,
  },
  {
    key: "open_recommendations",
    title: "Open recommendations",
    metric: (d) => d.open_recommendations,
    format: integerFormatter.format,
  },
  {
    key: "net_cost_impact",
    title: "Net cost impact",
    metric: (d) => d.net_cost_impact,
    format: currencyFormatter.format,
  },
] as const;

interface PanelCardDef {
  /** Also this card's `DrillSpec.id` — 1:1, unlike the KPI cards' KPI_DRILL_MAP indirection. */
  specId: string;
  title: string;
  render: (data: DashboardSummary) => ReactNode;
}

const PANEL_CARD_DEFS: readonly PanelCardDef[] = [
  {
    specId: "health-mix",
    title: "Inventory health mix",
    render: (data) => <HealthMixDonut slices={data.by_criticality} labelFor={criticalityLabel} />,
  },
  {
    specId: "sl-investment",
    title: "Service level vs. investment",
    render: (data) => (
      <SlInvestmentPanel byCriticality={data.by_criticality} labelFor={criticalityLabel} />
    ),
  },
  {
    specId: "ata-risk",
    title: "Risk by ATA chapter",
    render: (data) => <AtaRiskList chapters={data.by_ata} />,
  },
  {
    specId: "priority-actions",
    title: "Priority actions",
    render: (data) => <PriorityActionsPreview shortages={data.top_shortages} />,
  },
] as const;

/**
 * Overview — Slice S4 (dashboard) + F3 (in-place drill panels). Sourced from
 * GET /v1/tenants/{tenant}/dashboard via useDashboard(). Every displayed
 * number flows through Metric/ProvChip (docs/DESIGN-SYSTEM.md §4).
 *
 * F3: every card — the 8 KPIs and the 4 panel cards (health-mix,
 * SL-investment, ATA-risk, priority-actions) — is a `DrillableCard` whose
 * header discloses the full breakdown behind its headline number via an
 * in-place `DrillPanel`, driven by the `DRILL_SPECS`/`KPI_DRILL_MAP`
 * registry (src/features/overview/drillSpecs.ts). This also surfaces
 * `by_part_class`/`by_tier` — two breakdowns the BFF has always computed
 * but that no component rendered before this slice.
 *
 * `openDrillId` is a single string (not a Set) — a deliberate single-open
 * invariant: opening one card's panel closes whichever was already open, so
 * only one row-wide expansion is ever on screen at a time. Each `DrillPanel`
 * is rendered as a `col-span-full` sibling grid item immediately after the
 * card that opened it (both KPI and panel cards share this pattern via
 * `DrillCard`) — CSS grid always gives a `col-span-full` item its own row,
 * so the panel reads as a full-width expansion right below wherever the
 * click happened, regardless of that card's column position in the grid.
 */
export function Overview() {
  const { data, isPending, isError, error, refetch, dataUpdatedAt } = useDashboard();
  const [openDrillId, setOpenDrillId] = useState<string | null>(null);
  const triggerRefs = useRef<Map<string, RefObject<HTMLButtonElement>>>(new Map());

  function triggerRefFor(id: string): RefObject<HTMLButtonElement> {
    let ref = triggerRefs.current.get(id);
    if (!ref) {
      ref = { current: null };
      triggerRefs.current.set(id, ref);
    }
    return ref;
  }

  if (isPending) {
    return <QueryLoading label="Loading dashboard…" />;
  }

  if (isError) {
    return <QueryError label="Failed to load dashboard" error={error} onRetry={() => refetch()} />;
  }

  // Stamp with the query's real fetch time (dataUpdatedAt), not render-time
  // "now" — so a stale (staleTime: 60s) card's ProvChip tooltip honestly
  // ages instead of always reading "just now" (Slice S8 hardening).
  const provenance = dashboardProvenance(new Date(dataUpdatedAt));

  function toggleDrill(id: string) {
    setOpenDrillId((prev) => (prev === id ? null : id));
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold text-ink">Overview</h1>
        <p className="text-sm text-ink-2">Network inventory health, risk, and priority actions.</p>
      </header>

      {/* KPI cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {KPI_DEFS.map((kpi) => (
          <DrillCard
            key={kpi.key}
            id={kpi.key}
            title={kpi.title}
            specId={KPI_DRILL_MAP[kpi.key]}
            open={openDrillId === kpi.key}
            onToggle={() => toggleDrill(kpi.key)}
            triggerRef={triggerRefFor(kpi.key)}
            data={data}
            provenance={provenance}
            onClose={() => setOpenDrillId(null)}
          >
            <Metric metric={withProvenance(kpi.metric(data), provenance)} format={kpi.format} />
          </DrillCard>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {PANEL_CARD_DEFS.map((panelCard) => (
          <DrillCard
            key={panelCard.specId}
            id={panelCard.specId}
            title={panelCard.title}
            specId={panelCard.specId}
            open={openDrillId === panelCard.specId}
            onToggle={() => toggleDrill(panelCard.specId)}
            triggerRef={triggerRefFor(panelCard.specId)}
            data={data}
            provenance={provenance}
            onClose={() => setOpenDrillId(null)}
          >
            {panelCard.render(data)}
          </DrillCard>
        ))}
      </div>
    </div>
  );
}

interface DrillCardProps {
  /** This card's drill-state id (matched against `openDrillId`) and `DrillPanel`'s `id` suffix. */
  id: string;
  title: string;
  /** Which `DrillSpec` this card's panel renders — looked up from `DRILL_SPECS`. */
  specId: string;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement>;
  data: DashboardSummary;
  provenance: Provenance;
  children: ReactNode;
}

/**
 * One `DrillableCard` plus its conditionally-rendered `DrillPanel` sibling —
 * extracted so `Overview` doesn't repeat the "look up the spec, wire the
 * panel id, forward the trigger ref for focus restore" wiring once per KPI
 * card and once per panel card (12 call sites total).
 */
function DrillCard({
  id,
  title,
  specId,
  open,
  onToggle,
  onClose,
  triggerRef,
  data,
  provenance,
  children,
}: DrillCardProps) {
  const panelId = `overview-drill-panel-${id}`;
  const spec = open ? DRILL_SPECS.find((candidate) => candidate.id === specId) : undefined;

  return (
    <>
      <DrillableCard
        title={title}
        open={open}
        onToggle={onToggle}
        panelId={panelId}
        triggerRef={triggerRef}
      >
        {children}
      </DrillableCard>
      {spec && (
        <div className="col-span-full">
          <DrillPanel id={panelId} title={spec.title} onClose={onClose} restoreFocusTo={triggerRef}>
            <DrillContent spec={spec} data={data} provenance={provenance} />
          </DrillPanel>
        </div>
      )}
    </>
  );
}
