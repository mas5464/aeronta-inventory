# New Web UI — Build Plan (Trax Inventory Optimizer)

> Execute via superpowers:subagent-driven-development. Marries the new-UI spec to our
> real-eMRO pipeline. Spec set: `<session outputs>/inventory-optimizer/docs/`
> (PRD, ARCHITECTURE, DESIGN-SYSTEM, DATA-MODEL, API-SPEC, BUILD-PLAN). Existing-surface
> map + marriage analysis: `.superpowers/sdd/new-ui-synthesis.md`.

**Decisions (owner):** (A) Build the spec-faithful **`apps/web`** (React+Vite+TS+**Tailwind**+shadcn+
TanStack Query+Zustand) against our **existing Python FastAPI BFF** (`services/agent-spine/.../bff`),
extending the BFF with the missing endpoint groups. Do NOT rebuild the backend in Nest/Prisma.
(B) Target **full 7-view breadth**, sequenced as tracer-bullet slices — each slice lands one
demoable increment on real eMRO data.

## Architecture of the marriage
- **Data/engine:** unchanged — real eMRO extract → feature store → `trax_io_reco` (+ forecasting).
  Reuse `PlannerStore`/`from_extract`/`from_snapshot`. Net-new backend logic (scenario solve,
  forecast/SL aggregates, feed health) is added as new modules in `services/` and exposed via the BFF.
- **API:** extend the FastAPI BFF (`bff/app.py` + `bff/models.py`) with new route groups. Keep the
  existing 12 routes. Emit OpenAPI (the spec's API contract) for the TS client.
- **Web:** new `apps/web` (sibling to `apps/planner-ui`, which stayed as-is at the time — later
  retired once `apps/web` reached feature parity). Typed client generated/hand-written against the
  BFF. TanStack Query for all data (no hardcoded arrays). Zustand for UI state.
- **Provenance invariant:** the spec wants per-metric provenance (`MetricValue`/`ProvChip`). Our data
  is per-recommendation evidence. Bridge: a `MetricValue<T> = { value, provenance }` wrapper on the web
  side + BFF responses carrying a `provenance` object (feed id, as-of, source) per surfaced metric.
  Where a true per-scalar source isn't available yet, stamp the extract's `manifest` (feed=eMRO,
  extract_date) as provenance — honest and uniform.
- **Persistence:** in-memory today. Durable stores (Scenarios, AuditEvents, FeedHealth) are added only
  where a slice needs them; start with an in-memory repo behind an interface, swap to SQLite/Postgres
  when durability is actually required (deferred per slice, not up front).

## Global constraints
- Real eMRO data stays gitignored; no secrets committed. Docker scoped to `trax-io-planner`.
- Every slice green before the next: `apps/web` → `npm test && npm run build && tsc -b`; BFF →
  `uv run --extra dev --extra bff pytest` + `ruff`. Cross-package uv edits → `uv sync --extra … --reinstall`.
- Provenance on every displayed metric (spec invariant). No hardcoded data arrays in the UI.
- Follow DESIGN-SYSTEM.md tokens/components exactly; accessibility (WCAG 2.1 AA) per component.

## Slices (each ≈ one SDD task, DB/engine → BFF → web → test)

### S1 — Foundation: apps/web + design system + provenance + one real KPI
Scaffold `apps/web` (Vite+React+TS+Tailwind+shadcn, TanStack Query, Zustand, Vitest, ESLint).
Implement DESIGN-SYSTEM tokens (color/type/spacing) + core primitives incl. **`ProvChip`** and
**`Metric`** (a metric can't render without provenance — enforce by types + a test). Typed BFF client
(`lib/api`) hitting our real BFF. Prove the pipe: one real KPI (e.g. dashboard `parts`/`net_cost`)
renders value + ProvChip from a live BFF call. *Done when:* `apps/web` builds, one real KPI + ProvChip
renders from the BFF, tests green.

### S2 — Part Drill-Down (read path)
BFF: reuse `/parts/{pn}/{location}` (PartContext) + detail; add any missing stats. Web: drill-down
route — header, stat cards (stock-by-location, demand trend, lead time, open orders, vendor cost),
each metric with inline provenance. *Done when:* a real PN shows real stock + drivers, each with a ProvChip.

### S3 — AI Recommendations + Workbench (the core loop)
BFF: reuse the paged queue (`/recommendations`), detail, approve/reject/defer/bulk, history, rollback,
killswitch. Web: **Workbench** (ranked worklist — pill filters, confidence bars, impact, accept/adjust/
override/dismiss → audit) and **AI Recommendations** (cards: rec→reason→action, cycle summary, driver
weights). Server-side pagination + real actions. *Done when:* a planner works the ranked real-eMRO list,
every action audited. **First demo milestone.**

### S4 — Overview dashboard
BFF: extend `/dashboard` (or add `/overview`) for KPI cards, SL-vs-investment series, health-mix donut,
priority-actions preview, ATA risk — all provenance-backed. Web: Overview view. *Done when:* Overview
renders real aggregates, every metric provenance-backed.

### S5 — Forecast & Service Levels
Engine/BFF: expose forecast accuracy, actual-vs-forecast band, differentiated SL policy by criticality,
method coverage (from `services/forecasting` + reco). New `/forecast` route group. Web: Forecast view.
*Done when:* real forecast/SL metrics render (needs FLEET_UTILIZATION/causal + demand history — already
extracted; MAINTENANCE_SCHEDULE deferred/stubbed with honest provenance).

### S6 — What-If Scenarios (largest net-new)
Engine: a scenario solver over the reco/optimizer (sliders: SL target, budget, TAT, scope) with a
live-resolve latency budget; persist scenarios (in-memory repo first). BFF: `POST /scenarios/solve`,
save/compare/commit (audited). Web: sliders → projected outcome vs plan, cost–service frontier,
save/compare/commit. *Done when:* a scenario solves live and renders a frontier; commit is audited.

### S7 — Data & Connections
BFF: feed-health endpoint over the 13-feed model (real for extracted feeds via the manifest; MISSING
feeds shown as not-connected with honest status). Web: health strip, 13-feed table (filterable),
recommended-feeds-to-add, part stat-sheet reference browser. *Done when:* the real feed-coverage state
renders truthfully (extracted vs missing).

### S8 — Hardening
a11y (WCAG 2.1 AA) pass, table virtualization for 40k+ SKUs, perf budgets, error/empty/stale states,
Playwright e2e of the accept-recommendation + commit-scenario flows. Trackers (CLAUDE/ROADMAP) + a
UAT plan for apps/web.

## Sequencing notes
- S1 is a hard prerequisite (sets conventions). S2–S4 reuse existing BFF endpoints → fast, high-value,
  real data early. S5–S7 need net-new backend and are heavier. S6 (Scenarios) is the biggest risk.
- Feeds we don't extract yet (REPAIR_ORDERS, RELIABILITY, MAINTENANCE_SCHEDULE, QUOTATIONS, CONTRACTS):
  surface honestly (not-connected) in S7; wire real extracts as follow-ups where the eMRO DB has them.
- Provenance is enforced from S1 so it can't be retrofitted.
