# Planner UI — richer part context, trends, and a portfolio dashboard

**Date:** 2026-07-01
**Sub-project:** #7 Planner UI ("Trax IO Review")
**Status:** Design approved
**Builds on:** the ops-console redesign (`apps/planner-ui`), the Planner BFF ([ADR-0011](../../adr/2026-06-28-0011-planner-ui-bff.md)), the audited writeback seam ([ADR-0010](../../adr/2026-06-28-0010-audited-writeback-seam.md)).

---

## 1. Problem & goal

Planners can't see the **inventory reality** behind a recommendation: part description, total on hand, the real need (shortage), demand over the horizon, demand trend over time, stock breakdown, lead time — and there is no portfolio dashboard or trend analysis. The UI shows only the recommendation summary.

**Goal:** surface as much real part/inventory data as is useful, in three phases delivered under one spec:

- **A — enrich the queue + detail** with part context the engine already computes.
- **C — a per-part context view + demand trend**, served from the feature store.
- **B — a portfolio Dashboard** section with aggregate metrics and charts.

Frontend + a thin, additive BFF layer. No new data pipeline, no engine change.

---

## 2. Background — what data exists (verified)

- The `Recommendation` object the BFF already retains carries **`description`, `current_stock`, `shortage_quantity`, `recommended_location`, `horizon_days`, `projected_demand`, `current_policy`, `policy`** — but the wire models (`QueueRow`/`RecommendationDetail`) drop the first five. (recommendation-engine `contracts/recommendation.py`.)
- `PlannerStore.from_extract` builds an in-memory **`FeatureStoreClient`** (via `build_stores_from_extract`) and **discards it** after the engine run. It exposes per-`(tenant, pn[, location])` getters: `get_part_attributes`, `get_criticality`, `get_stock_position`, `get_current_policy`, `get_lead_time_distribution`, `get_open_orders_snapshot`, `get_demand_history`, `get_vendor_economics` (feature-store `client.py`).
- Feature-store schemas already model everything we want to show: `StockPosition` (on_hand, serviceable, in_repair, allocated, rental, loan), `PartAttributes` (description, ata_chapter, part_class, shelf_life_days, hazardous_material, tool_control_item, fleet_effectivity_tail_count), `Criticality` (canonical_tier), `CurrentPolicy` (rop/eoq/safety_stock/max_stock/replenishment_lead_days), `LeadTimeDistribution` (mean/p50/p90/p99/n), `OpenOrdersSnapshot` (total_open_qty + rows), `DemandHistory` (observations: bucket, period_start, removals, issues), `VendorEconomics` (unit_cost, min_order_qty, vendor).
- Sample extract is small (3 parts / 4 `PN×Location`) but has a **120-row demand timeseries** — enough to prove trends. Real tenants have the full portfolio.

**Architecture decision (Approach 1):** retain the feature store in `PlannerStore` and add small read endpoints over it. The portfolio view falls out of iterating the fs's `(pn, location)` universe.

---

## 3. Backend (BFF)

### 3.1 Retain the feature store
`PlannerStore.from_extract` already builds `fs, inv, tenant, keys` (via `build_stores_from_extract`), where `keys` is the **full `(pn, location)` universe** from the extract — not just the parts that produced a recommendation. Keep `fs`, the `TenantContext`, and `keys` on the instance. All new reads go through `fs`; the dashboard iterates `keys` for a genuine portfolio view. No change to the engine run or the existing queue/approve/history endpoints.

### 3.2 Phase A — enrich existing wire models
Add to `QueueRow` **and** `RecommendationDetail` (all already on `Recommendation`):
`description: str`, `current_stock: int`, `shortage_quantity: float`, `recommended_location: str | None`, `horizon_days: int`.
Populate in `PlannerStore._row` / `.detail`. No new endpoint.

### 3.3 Phase C — `GET /v1/tenants/{tenant}/parts/{pn}/{location}` → `PartContext`
Assembled from `fs` for one key (each getter wrapped so a missing group degrades to `None`, never 500):
- `attributes`: description, ata_chapter, part_class, shelf_life_days, hazardous_material, tool_control_item, criticality_tier.
- `stock`: `StockBreakdown` — on_hand, serviceable, in_repair, allocated, rental, loan.
- `current_policy` / `proposed_policy`: rop/eoq/safety_stock/max_stock (proposed from the recommendation if one exists for the key, else `None`).
- `lead_time`: `LeadTimeView` — mean_days, p50, p90, p99, sample_n.
- `open_orders`: `total_open_qty` + `rows: OpenOrderView[]` (order_id, order_type, vendor, qty_open, expected_rcv_date).
- `demand`: `DemandSummary` — total_24mo, per-period `points: DemandPoint[]` (period_start, removals, issues, total) sorted ascending.
- `unit_cost`: from `VendorEconomics` (for on-hand value + cost display).
404 if the key is unknown to `fs`.

### 3.4 Phase B — `GET /v1/tenants/{tenant}/dashboard` → `DashboardSummary`
Aggregate over the retained `keys` (the full `(pn, location)` portfolio), reading each key's stock/attributes/criticality/demand from `fs`; recommendation-derived figures (shortage, projected demand, cost, autonomy tier) come from the store's entries and default to 0/None for portfolio parts without a recommendation:
- Totals: `parts`, `total_on_hand`, `total_on_hand_value`, `total_shortage`, `total_projected_demand`, `aog_exposure` (count aog ≥ 3), `open_recommendations`, `net_cost_impact`.
- Breakdowns (each a list of `{key, count, on_hand, shortage}`): by `criticality_tier`, by `ata_chapter`, by `part_class`, by autonomy `tier`.
- `top_shortages: PartShortfall[]` — top-N `(pn, location, shortage, on_hand, projected_demand)`.
- `demand_vs_supply` — total projected demand vs total available, for the headline chart.

### 3.5 New pydantic wire models
`StockBreakdown`, `LeadTimeView`, `OpenOrderView`, `DemandPoint`, `DemandSummary`, `PartAttributesView`, `PartContext`, and `DashboardSummary` + its breakdown/`PartShortfall` parts. All frozen, `extra="forbid"`, mirroring the existing `bff/models.py` style.

---

## 4. Frontend

### 4.1 Client + fakes
Extend `PlannerClient`: `getPartContext(tenant, pn, location): Promise<PartContext>` and `getDashboard(tenant): Promise<DashboardSummary>`. TS mirrors of every new model. `FakePlannerClient` returns seeded part contexts + a computed dashboard (from the sample rows) so offline dev + Vitest cover it.

### 4.2 Phase A — enriched queue + detail
- `QueueTable`: add **On hand** and **Need** (shortage) columns; show the description under/next to the part name (or via title). Keep sortable.
- `DetailPanel`: a part header (description · part class · ATA) and an "on hand N · need N · demand N over Hd days" strip above the current→proposed policy.

### 4.3 Phase C — part context drawer + demand trend
Selecting a row lazily calls `getPartContext`. The detail becomes a **right-side drawer** (the deferred drawer, built now) showing: attributes, **stock breakdown** (on-hand split), lead-time (mean + p50/p90/p99), open orders, the policy diff, evidence + writeback history (existing), and a **demand-trend chart** — a 24-month line/bar of removals+issues (dependency-free inline SVG like `ChartRow`, or reuse a small chart util). Loading + error states; a stale-fetch guard by `(pn, location)` token.

### 4.4 Phase B — Dashboard section
The nav-rail **Dashboard** goes live at `#/dashboard` (route added to the HashRouter). A `DashboardView` fetches `/dashboard` and renders: KPI cards (parts, on-hand value, total need, AOG exposure, net cost), a demand-vs-supply chart, breakdown charts (by criticality / ATA / part class), and a **top-shortages** table linking back to the part drawer. Nav-rail Review vs Dashboard drive the two routes.

---

## 5. Data flow

```
from_extract → build fs + run engine → PlannerStore{ entries(rec,outcome), fs, tenant }
  queue        → enriched QueueRow (from Recommendation)
  select row   → GET /parts/{pn}/{loc} → PartContext (fs reads) → drawer + trend
  Dashboard    → GET /dashboard → DashboardSummary (aggregate over fs keys)
```

---

## 6. Scope & deferrals
**In:** retain fs; enrich wire models (A); `/parts` + PartContext + part drawer + demand trend (C); `/dashboard` + Dashboard section (B); new models + TS mirrors + fakes; tests; Docker redeploy.
**Deferred:** interchange-group rollup view; vendor/sourcing detail; per-period drill-down beyond 24 months; real-portfolio pagination/virtualization (sample is tiny); auth/persistence (per ADR-0011); wash-rate/causal (unused in v1); write actions from the Dashboard.

## 7. Testing
- **BFF (pytest):** `PartContext` assembled correctly from a seeded fs (stock/lead-time/demand/attributes present; missing-group → `None`, no 500; 404 on unknown key); `DashboardSummary` totals + breakdowns + top-shortages match a known seed; enriched `QueueRow`/`RecommendationDetail` carry the new fields; tenant isolation on the new endpoints.
- **UI (Vitest):** client `getPartContext`/`getDashboard` (http URL + fake); enriched table columns; part drawer renders stock/lead-time/trend + lazy-load + stale guard; demand-trend chart from points; DashboardView KPIs/breakdowns/top-shortages; `#/dashboard` route.
- Full suites green; Docker image rebuilt on 8088.

## 8. Acceptance
A planner selecting a row sees the part's description, on-hand breakdown, need, lead time, open orders, and a 24-month demand trend; the Dashboard shows portfolio totals, breakdowns, and top shortages; all served from the retained feature store with no engine change; BFF + UI suites green.
