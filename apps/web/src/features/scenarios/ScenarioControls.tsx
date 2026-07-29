import type { ScenarioParams, ScenarioScopeKind } from "@/lib/api/types";

export interface ScenarioControlsProps {
  params: ScenarioParams;
  onChange: (params: ScenarioParams) => void;
}

const CRITICALITY_TIER_OPTIONS = [1, 2, 3, 4, 5];

const pctFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

/**
 * The What-If sliders (PRD §6.5): target service level, inventory budget cap,
 * repair-TAT assumption (lead-time delta), and scope (all / criticality tier / ATA
 * chapter). Plain range/number/select inputs — no slider dependency is installed in
 * this app, matching its dependency-free-primitives convention (DemandTrend,
 * HealthMixDonut). The parent debounces the resulting solve.
 */
export function ScenarioControls({ params, onChange }: ScenarioControlsProps) {
  const slValue = params.service_level_target ?? 0.95;
  const tatValue = params.lead_time_delta_pct ?? 0;
  const scope: ScenarioScopeKind = params.scope ?? "all";

  return (
    <div className="flex flex-col gap-5" role="group" aria-label="What-if scenario controls">
      <label className="flex flex-col gap-1.5 text-sm">
        <span className="flex items-center justify-between text-ink">
          <span className="font-medium">Target service level</span>
          <span className="tabular-nums text-ink-2">{pctFormatter.format(slValue)}</span>
        </span>
        <input
          type="range"
          min={0.8}
          max={0.999}
          step={0.001}
          value={slValue}
          onChange={(e) =>
            onChange({ ...params, service_level_target: Number(e.target.value) })
          }
          aria-label="Target service level"
          className="h-2 w-full cursor-pointer accent-ink"
        />
      </label>

      <label className="flex flex-col gap-1.5 text-sm">
        <span className="flex items-center justify-between text-ink">
          <span className="font-medium">Repair-TAT / lead-time assumption</span>
          <span className="tabular-nums text-ink-2">
            {tatValue >= 0 ? "+" : ""}
            {Math.round(tatValue * 100)}%
          </span>
        </span>
        <input
          type="range"
          min={-0.5}
          max={1}
          step={0.05}
          value={tatValue}
          onChange={(e) => onChange({ ...params, lead_time_delta_pct: Number(e.target.value) })}
          aria-label="Repair-TAT / lead-time delta"
          className="h-2 w-full cursor-pointer accent-ink"
        />
      </label>

      <label className="flex flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink">Inventory budget cap (optional)</span>
        <input
          type="number"
          min={0}
          step={1000}
          placeholder="No cap"
          value={params.budget_cap ?? ""}
          onChange={(e) =>
            onChange({
              ...params,
              budget_cap: e.target.value === "" ? null : Number(e.target.value),
            })
          }
          aria-label="Inventory budget cap"
          className="h-9 rounded-control border border-line bg-panel px-2 text-sm text-ink"
        />
      </label>

      <div className="flex flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink">Scope</span>
        <select
          value={scope}
          onChange={(e) => {
            const nextScope = e.target.value as ScenarioScopeKind;
            onChange({ ...params, scope: nextScope, scope_value: null });
          }}
          aria-label="Scenario scope"
          className="h-9 rounded-control border border-line bg-panel px-2 text-sm text-ink"
        >
          <option value="all">All parts</option>
          <option value="criticality_tier">By criticality tier</option>
          <option value="ata_chapter">By ATA chapter</option>
        </select>

        {scope === "criticality_tier" && (
          <select
            value={params.scope_value ?? ""}
            onChange={(e) => onChange({ ...params, scope_value: e.target.value || null })}
            aria-label="Criticality tier"
            className="h-9 rounded-control border border-line bg-panel px-2 text-sm text-ink"
          >
            <option value="">Select a tier…</option>
            {CRITICALITY_TIER_OPTIONS.map((tier) => (
              <option key={tier} value={tier}>
                Tier {tier}
              </option>
            ))}
          </select>
        )}

        {scope === "ata_chapter" && (
          <input
            type="text"
            value={params.scope_value ?? ""}
            onChange={(e) => onChange({ ...params, scope_value: e.target.value || null })}
            placeholder="e.g. 32"
            aria-label="ATA chapter"
            className="h-9 rounded-control border border-line bg-panel px-2 text-sm text-ink"
          />
        )}
      </div>
    </div>
  );
}
