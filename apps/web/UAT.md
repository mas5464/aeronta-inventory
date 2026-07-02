# Trax Inventory Optimizer (`apps/web`) — UAT Test Plan

**Living document** — run before every release. Each manual case lists the automated test that
already covers it (`Auto` column), so this plan is both a manual UAT checklist and the map for the
automated regression gate. Update it whenever a feature is added or changed.

- **Component:** `apps/web` (React 18 + TS + Vite + Tailwind + shadcn/ui + TanStack Query, BFF =
  `trax_io_spine.bff`) — the spec-faithful Trax Inventory Optimizer UI, distinct from and alongside
  `apps/planner-ui` (see [apps/planner-ui/UAT.md](../planner-ui/UAT.md)). `apps/web` renders the
  full PRD §6 surface (7 views) directly over the same BFF.
- **Last validated against:** Slice S8 (Hardening) — 142 Vitest tests green
- **Owner:** Miguel Sosa

---

## 1. How to run the manual UAT

### Live mode (this app has no offline/fake-client mode — always talks to a real BFF)
```bash
# terminal 1 — start the BFF (from services/agent-spine)
cd services/agent-spine
EXTRACT_DIR=../recommendation-engine/examples/extract_sample \
  uv run --extra bff uvicorn trax_io_spine.bff.asgi:app --port 8001

# terminal 2
cd apps/web
npm install            # first time only
npm run dev
# open http://localhost:5173 (VITE_BFF_URL defaults to http://localhost:8001)
```
All 7 views render real engine output computed from the sample extract (`~21,215` keys) — there is
no seeded/fake client to reset between runs; reloading refetches from the live BFF.

### Docker (full-stack, real eMRO-shaped data)
```bash
docker compose up --build web bff   # repo root, project trax-io-planner
# open http://localhost:8089 (apps/web); apps/planner-ui stays on :8088
```
Never touches `oracle19c`/MySQL — scoped to this project's compose file only.

### Automated regression gate (run for every release)
```bash
cd apps/web && npm test && npm run build && npm run lint   # 142 tests + typecheck+build + eslint
```
Full-stack regression (backend the UI depends on): `cd services/agent-spine && uv run --extra bff
pytest` (agent-spine `--extra bff`, unchanged by this slice — `apps/web` is a pure frontend
consumer of the same BFF `apps/planner-ui` already exercises).

### Best-effort e2e (Playwright)
```bash
cd apps/web && npx playwright install chromium   # first time only, downloads a browser
npm run e2e
```
One spec (`e2e/workbench-accept.spec.ts`): loads the Workbench against a **route-mocked** BFF (no
real backend needed — `page.route("**/v1/**")` intercepts every request), accepts a recommendation,
and asserts the row leaves the list. Runs against a dedicated dev-server port (`5190`, not the
repo's usual `5173`) to avoid colliding with any other project's dev server that might already be
running on the shared machine. Further e2e scenarios (a11y keyboard sweep, the other 6 views,
error/retry paths) are deferred — see §5.

---

## 2. Seed / fixture reference (live mode, sample extract)

There is no fixed seed to reference exact numbers against — `apps/web` always talks to a live BFF
computing over whatever extract it was started with (the sample extract by default: `~21,215`
`(pn, location)` keys, 4 seeded pending recommendations on `HYD-PUMP-001`/`FILTER-EXP-042`/
`VALVE-MOD-117`). For an exact fixed dataset to eyeball against, see
[apps/planner-ui/UAT.md §2](../planner-ui/UAT.md) — both apps compute from the identical sample
extract's engine output, so those documented values (e.g. `HYD-PUMP-001 · YYZ`: ROP 6→9, EOQ 10→12)
apply here too when both point at the same BFF instance.

**Recording results:** mark each case **Pass / Fail / Blocked**. For a Fail, capture the case ID,
the actual result, a screenshot, and the browser/OS.

---

## 3. Test cases

> Legend — **Auto** column: the automated test that covers the case (`file ▸ test name`), or
> `MANUAL` (real-browser/visual only — not automated), or `TO-AUTOMATE`.

### A. App shell & navigation

| ID | Steps | Expected | Auto |
|---|---|---|---|
| A1 | Open the app | Header "Trax Inventory Optimizer"; a primary nav with 6 items (Overview / Workbench / AI Recommendations / Forecast & Service Levels / What-If Scenarios / Data & Connections) | App ▸ renders the header and every nav item |
| A2 | Observe the active nav item | The current view's nav link is visually distinct and carries `aria-current="page"`; no other item does | App ▸ marks the active nav item with aria-current=page… |
| A3 | Click a different nav item | URL hash updates (`#/workbench`, etc.); `aria-current` moves to the clicked item | App ▸ clicking a nav item navigates… |
| A4 | Open a non-root URL directly (e.g. `#/data`) | Deep-links straight to that view | App ▸ deep-links directly to a non-root route… |
| A5 | Tab through the nav with the keyboard only | Every nav item is reachable, in document order, with a visible focus ring | App ▸ every nav item is keyboard-focusable with a visible focus-visible ring class |

### B. Overview (`/`)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| B1 | Open `/` | Loading state, then 8 KPI cards (Parts, Total on-hand, On-hand value, Total shortage, Projected demand, AOG exposure, Open recommendations, Net cost impact), each with a `ProvChip` | Overview ▸ shows a loading state, then the real Parts and Net cost impact KPIs with provenance |
| B2 | Read every KPI card | All 8 titles present | Overview ▸ renders all KPI cards from the DashboardSummary |
| B3 | Read the health-mix donut, ATA risk list, priority actions | Donut has an accessible `role="img"` label summarizing every slice by name+count+%; ATA risk list ranks by shortage; priority actions link into Part Drill-Down + a "View all in Workbench" link | Overview ▸ renders the health-mix donut, ATA risk list, and priority-actions preview… |
| B4 | Read "Service level vs. investment" | Honest banner: "not yet connected… on-hand coverage by criticality band" (no fabricated SL number) | Overview ▸ renders the SL-vs-investment panel with an honest not-yet-connected disclosure |
| B5 | Stop the BFF, reload | Error banner "Failed to load dashboard: …" with a **Retry** button; clicking Retry re-fetches | client/QueryState pattern ▸ Overview uses `<QueryError>` (see QueryState.test.tsx) |
| B6 | With a dataset that has zero ATA/shortage rows | Each panel (`AtaRiskList`, `PriorityActionsPreview`, `HealthMixDonut`, `SlInvestmentPanel`) shows its own explicit empty-state text, not a blank area | AtaRiskList/PriorityActionsPreview/HealthMixDonut/SlInvestmentPanel ▸ …empty state… |

### C. Part Drill-Down (`/parts/:pn/:location`)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| C1 | Navigate from a Priority Actions row (or Workbench row) into a part | Header (PN, location, criticality/ATA/part-class/hazmat/tool-control badges), stat cards (Stock position, Policy current→proposed, Need/shortage, Projected demand, Lead time, Open orders, Unit cost) each with a `ProvChip`, demand-trend chart, open-orders table | PartDrillDown ▸ shows a loading state, then renders header, stat metrics, and provenance chips |
| C2 | Open an unknown `(pn, location)` | Error banner "Failed to load part …" with a **Retry** button | PartDrillDown ▸ renders an error state when the BFF call fails (unknown part) |
| C3 | Open a part with no demand history / no open orders | Each section shows its own empty message ("No demand history…", "No open orders.") rather than crashing | PartDrillDown ▸ renders empty states gracefully when demand/open orders are absent |
| C4 | Inspect the open-orders table headers (devtools) | `<th scope="col">` on every header column | (render-level; see PartDrillDown.tsx `scope="col"`) |

### D. Workbench (`/workbench`) — core loop

| ID | Steps | Expected | Auto |
|---|---|---|---|
| D1 | Open `/workbench` | Server-paged ranked worklist (`GET …/recommendations?limit=25&offset=0`), tier/type/AOG pill filters, confidence bars, cost impact, priority, a pager showing "1–25 of N" | Workbench ▸ renders the ranked worklist with confidence bars and a pager |
| D2 | Click a row's Accept button | Row's approve fires `POST …/approve`; on success the queue re-fetches and the row leaves the list | Workbench ▸ fires Accept/Defer/Dismiss row actions |
| D3 | Click Dismiss, choose a reason, Confirm dismiss | Inline reason dialog (role=dialog), fires `POST …/reject` with the chosen reason | Workbench ▸ fires Accept/Defer/Dismiss row actions |
| D4 | Engage the kill switch | Paused banner; Accept disabled network-wide until resumed | Workbench ▸ disables Accept when the kill switch is engaged and shows a paused banner |
| D5 | Apply a tier/type/AOG pill filter | Rows narrow client-side over the loaded page | Workbench ▸ applies pill filters client-side over the loaded page |
| D6 | Click "Accept high-confidence (N)" | Bulk-approves the ≥80%-confidence, approvable rows on the current page | Workbench ▸ bulk-approves the high-confidence candidates on the loaded page |
| D7 | Click Next/Previous | Pager advances/retreats; fetches the next offset | Workbench ▸ advances the pager on Next |
| D8 | Inspect "Adjust (coming soon)" | Always disabled with an explanatory tooltip — no edit-before-accept endpoint exists yet | Workbench ▸ disables the Adjust control as coming-soon |
| D9 | Stop the BFF, reload | Error banner "Failed to load workbench: …" with a **Retry** button | (QueryError wiring in Workbench.tsx) |
| D10 | Inspect the table headers (devtools) | `<th scope="col">` on every column; a `<caption class="sr-only">` names the table + current page range | (render-level; see Workbench.tsx) |
| D11 | Load a 200-row page (`MAX_PAGE_SIZE`) | All 200 rows render (no rows windowed out by a virtualization library), promptly | Workbench ▸ renders a full MAX_PAGE_SIZE (200-row) page smoothly, with no virtualization |
| D12 | Inspect `queueView.ts`'s pagination constants | `MAX_PAGE_SIZE = 200`; the Workbench's `PAGE_SIZE` (25) is bounds-checked against it at module load | queueView ▸ MAX_PAGE_SIZE (large-table / pagination strategy) |
| D13 | Open the Dismiss dialog, press **Escape** | Dialog closes without submitting; focus returns to the row's Dismiss button | RejectDialog ▸ closes (calls onCancel) when Escape is pressed |
| D14 | Open the Dismiss dialog, Tab through its controls | Focus wraps from the last control back to the first (Reason select) and vice versa — never escapes the dialog | RejectDialog ▸ traps Tab within the dialog's controls… |

### E. AI Recommendations (`/recommendations`)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| E1 | Open `/recommendations` | Cycle summary (counts by type + AOG), "How the optimizer decides" driver panel (evidence-kind frequency, explicitly labeled as a proxy, not calibrated weights), explainable cards (why → evidence → impact/qty/confidence → current vs proposed policy → Accept/Dismiss) | AiRecommendations ▸ renders explainable cards…, cycle summary, and driver panel |
| E2 | Click Accept / Dismiss on a card | Fires approve/reject | AiRecommendations ▸ fires Accept and Dismiss actions from a recommendation card |
| E3 | Engage the kill switch | Accept disabled on every card, paused banner shown | AiRecommendations ▸ disables Accept when the kill switch is engaged |
| E4 | With zero pending recommendations | "No pending recommendations. You're all caught up." (not a misleading "loading" message) | (AiRecommendations.tsx `topRows.length === 0` branch) |
| E5 | Stop the BFF, reload | Error banner with a **Retry** button | (QueryError wiring in AiRecommendations.tsx) |

### F. Forecast & Service Levels (`/forecast`)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| F1 | Open `/forecast` | KPI pair (SKUs on ML/statistical forecast, SL policy tiers configured), service-level-by-criticality table, forecast-method coverage bars, "Network actual vs. forecast" | ForecastServiceLevels ▸ shows a loading state, then the forecast KPIs with provenance |
| F2 | Read the SL policy table | Real per-tier targets + SKU counts + an honest coverage proxy (not a fabricated fill-rate) | ForecastServiceLevels ▸ renders the service-level policy table… |
| F3 | Read the method-coverage bars | Real regime→method mapping from the deterministic classifier | ForecastServiceLevels ▸ renders forecast-method coverage bars… |
| F4 | Read the accuracy band | Explicit "not yet connected" banner (no backtest at serve time) alongside the one truthful actual-vs-projected proxy table | ForecastServiceLevels ▸ renders the accuracy band with an honest not-yet-connected disclosure |
| F5 | Stop the BFF, reload | Error banner "Failed to load forecast: …" with a **Retry** button | ForecastServiceLevels ▸ renders an error state when the BFF call fails |

### G. What-If Scenarios (`/scenarios`)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| G1 | Open `/scenarios` | Levers (SL target / lead-time delta / budget cap / scope sliders), debounced live solve, projected outcome vs. current plan | Scenarios ▸ solves the default scenario on mount and renders the outcome with provenance |
| G2 | Move the SL slider | Re-solves (debounced ~350ms) | Scenarios ▸ re-solves (debounced) when the service-level slider changes |
| G3 | Read the cost-service frontier chart | Accessible SVG, current-plan + proposed-scenario markers, legend | Scenarios ▸ renders the cost-service frontier chart once solved; ScenarioFrontierChart ▸ (component tests) |
| G4 | Read the skipped-keys disclosure | Shown when the solve can't score every key (missing data), hidden otherwise | Scenarios ▸ shows the skipped-keys honest disclosure; ScenarioOutcomePanel ▸ shows/does not show… |
| G5 | Set a budget cap below the proposed investment | Warning banner: proposed investment exceeds the cap | Scenarios ▸ shows a budget-cap-exceeded warning…; ScenarioOutcomePanel ▸ shows a budget-cap-exceeded alert… |
| G6 | Name and save a scenario | Save acknowledgement; scenario appears in Saved scenarios | Scenarios ▸ saves a named scenario and shows a save acknowledgement |
| G7 | Click Commit on a saved (draft) scenario | Confirm dialog appears ("Commit as the tenant's target plan? No eMRO writeback occurs.") | SavedScenarios ▸ requires a confirm step before committing a draft scenario |
| G8 | Press **Escape** in the commit-confirm dialog | Dialog closes, `onCommit` is NOT called, focus returns to that row's Commit button (not lost to the page) | SavedScenarios ▸ closes the commit confirm dialog (WCAG 2.1 AA) when Escape is pressed… |
| G9 | Click Cancel in the commit-confirm dialog | Same as Escape — closes without committing | SavedScenarios ▸ cancels the commit confirm dialog without calling onCommit |
| G10 | Select two saved scenarios to compare | Comparison table (Service level / Projected investment / Investment delta / Skipped parts) | SavedScenarios ▸ shows a comparison table once exactly two scenarios are selected |
| G11 | Select a scope needing a value (criticality tier / ATA chapter) without picking one | "Select a scope value to solve this scenario." — no solve fires | ScenarioControls ▸ shows a criticality-tier select…/ATA-chapter text input… |
| G12 | Stop the BFF, reload | Error banner "Failed to solve scenario: …" with a **Retry** button that re-issues the same solve | Scenarios ▸ renders an error state when the initial solve fails |
| G13 | With zero saved scenarios | "No saved scenarios yet." | SavedScenarios ▸ renders an empty state when there are no saved scenarios |

### H. Data & Connections (`/data`)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| H1 | Open `/data` | Health strip (Connected / Partial / Not connected counts), extract date, a filterable 13-feed table | DataConnections ▸ shows a loading state, then the health strip with provenance |
| H2 | Read the 13-feed table | Every feed's true connection status (derived from the real extract-domain registry, not a spec fiction) | DataConnections ▸ renders all 13 feeds in the table with truthful statuses |
| H3 | Filter by status | Table narrows to matching feeds | DataConnections ▸ filters the feed table by status |
| H4 | Read "Recommended feeds to add" | Only `not_connected` feeds, ranked by impact (reliability first) | DataConnections ▸ renders the recommended-feeds-to-add panel… |
| H5 | Use the part-statistics lookup box | Navigates to the existing Part Drill-Down (not a second, parallel browser) | DataConnections ▸ renders the part stat-sheet lookup as a link-out search box |
| H6 | Stop the BFF, reload | Error banner "Failed to load feed health: …" with a **Retry** button | DataConnections ▸ renders an error state when the BFF call fails |

### I. Provenance invariant (cross-cutting — all 7 views)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| I1 | Hover any `ProvChip` | Tooltip shows source, system of record, freshness ("Nm/Nh/Nd ago"), coverage %, confidence %; never color-only (status label text always present) | ProvChip ▸ renders the source and a status affordance that isn't color-only |
| I2 | Compare a high-confidence vs. low-confidence value's chip | Status downgrades from "good" to "warn"/"bad" as `min(confidence, coverage)` drops | ProvChip ▸ downgrades to warn/bad status for lower confidence or coverage |
| I3 | Attempt to render a bare number without provenance (code-level) | TypeScript rejects it — `Metric`'s `metric` prop only accepts `MetricValue<T>` | provenance ▸ type-level: Metric's `metric` prop only accepts MetricValue\<T\>… |
| I4 | Reload the Overview after ~90s (staleTime is 60s) | The KPI cards' `ProvChip` tooltips show a freshness older than "just now" — the real query fetch time (`dataUpdatedAt`), not render time | MANUAL (timing-dependent; see `useDashboard`'s `staleTime: 60_000` + `Overview.tsx`'s `new Date(dataUpdatedAt)`) |

### J. Accessibility / keyboard (WCAG 2.1 AA)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| J1 | Tab through any view with the keyboard only | All controls reachable & operable: nav, row selectors, Accept/Reject/Defer, filters, sliders, form inputs. Visible `:focus-visible` ring everywhere (nav links, buttons, the shared `<QueryError>` Retry button) | App ▸ every nav item is keyboard-focusable…; QueryError ▸ the Retry button has a visible focus-visible ring |
| J2 | Inspect a status badge / donut / chart | Never color-only — text label or numeric value always present alongside color (ConfidenceBar %, AOG badge text, HealthMixDonut legend, ScenarioFrontierChart legend, MethodCoverageBars/AtaRiskList/SlInvestmentPanel `aria-label`s) | ConfidenceBar/HealthMixDonut/ScenarioFrontierChart/ProvChip ▸ (component tests, "not color-only") |
| J3 | Inspect a data table's headers (devtools) | Every `<th>` has `scope="col"` (Workbench, Part Drill-Down open-orders, Feed table, Service-level table, Accuracy band, Saved-scenarios comparison table) | (render-level across the 6 tables; see each component's `scope="col"`) |
| J4 | Trigger a loading state | `role="status"` + `aria-live="polite"` | QueryLoading ▸ renders a role=status live region… |
| J5 | Trigger an error state | `role="alert"` (announced assertively) with a working **Retry** | QueryError ▸ renders a role=alert with the label + error message and a Retry button… |
| J6 | Open the Dismiss dialog (Workbench) or the commit-confirm dialog (Scenarios) | `role="dialog"` + `aria-modal="true"`; focus moves into the dialog on open, Tab traps within it, Escape closes it, focus returns to the trigger on close | RejectDialog ▸ (3 tests); SavedScenarios ▸ closes… when Escape is pressed…; useFocusTrap ▸ (5 tests) |
| J7 | Color-contrast spot check (light mode) | Text meets WCAG AA contrast | MANUAL (visual) |

### K. Edge cases & error handling

| ID | Steps | Expected | Auto |
|---|---|---|---|
| K1 | Any view, BFF returns non-2xx | `ApiError` carries the status + a parsed `detail` when present, else `statusText` | client ▸ throws an ApiError with status + detail on a non-OK response (×4 endpoints) |
| K2 | Any view, BFF unreachable (network error) | Generic error banner via `<QueryError>`, no unhandled crash, Retry available | (all 7 views' `isError` branches route through `<QueryError>`) |
| K3 | A malformed/non-`Error` rejection reaches `<QueryError>` | Falls back to "unknown error" text rather than crashing on `.message` | QueryError ▸ falls back to 'unknown error' for a non-Error thrown value |

---

## 4. Traceability & coverage summary

| Area | Cases | Automated | Manual-only |
|---|---|---|---|
| A App shell & navigation | 5 | 5 | — |
| B Overview | 6 | 6 | — |
| C Part Drill-Down | 4 | 4 | — |
| D Workbench (core loop) | 14 | 14 | — |
| E AI Recommendations | 5 | 5 | — |
| F Forecast & Service Levels | 5 | 5 | — |
| G What-If Scenarios | 13 | 13 | — |
| H Data & Connections | 6 | 6 | — |
| I Provenance invariant | 4 | 3 | I4 (staleTime timing) |
| J Accessibility | 7 | 6 | J7 (contrast) |
| K Edge/errors | 3 | 3 | — |

**Manual-only items to consider automating later:**
- I4 — real-clock staleTime/freshness-aging spot check.
- J7 — automated color-contrast (axe-core) in light mode; dark mode isn't wired in this app yet.

Everything else is already covered by the **142 Vitest tests**; keep this table in sync as cases
are added so "run the Vitest suite" remains a true automated proxy for this UAT.

---

## 5. e2e (Playwright) — status

**One spec landed** (`e2e/workbench-accept.spec.ts`, `npm run e2e`): Workbench, route-mocked BFF,
accept a recommendation, row leaves the list. This was verified working end-to-end during the S8
hardening slice.

**Deferred** (out of scope for S8 — future work):
- The other 6 views' e2e coverage (Overview, Part Drill-Down, AI Recommendations, Forecast, Scenarios, Data & Connections).
- A full keyboard-only traversal + screen-reader announcement sweep (J1/J2/J6 today are Vitest
  component-level; a real end-to-end keyboard trace would strengthen confidence further).
- Error/retry paths end-to-end (currently Vitest-level via mocked `fetch`).
- Automated color-contrast (axe-core) in a real browser.

---

## 6. Per-release checklist

1. `npm test` (142 green) · `npm run build` (tsc -b + vite) · `npm run lint` (eslint) clean.
2. Backend regression the UI depends on: `cd services/agent-spine && uv run --extra bff pytest`
   (unchanged by this slice — `apps/web` is a pure frontend consumer of the same BFF surface
   `apps/planner-ui` already exercises).
3. Best-effort: `npm run e2e` (1 Playwright spec — requires `npx playwright install chromium`
   once).
4. Smoke the live-mode build manually: cases A1, B1, D1, D2, G7, H1, I1, J6 (the critical path).
5. If any UI behavior changed, add/adjust the matching case here **and** its Vitest test in the
   same PR.
