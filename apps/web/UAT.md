# Trax Inventory Optimizer (`apps/web`) — UAT Test Plan

**Living document** — run before every release. Each manual case lists the automated test that
already covers it (`Auto` column), so this plan is both a manual UAT checklist and the map for the
automated regression gate. Update it whenever a feature is added or changed.

- **Component:** `apps/web` (React 18 + TS + Vite + Tailwind + shadcn/ui + TanStack Query, BFF =
  `trax_io_spine.bff`) — the spec-faithful Trax Inventory Optimizer UI, and the product's sole
  frontend. `apps/web` renders 9 authed views + the pre-auth signup wizard directly over the BFF.
- **Last validated against:** C5 (multi-tenant serving + scheduled recompute) — 381 Vitest tests
  green, verified live-in-a-local-emulator (see §1) end to end: sign-in page → signup wizard →
  fresh-tenant `GET /v1/auth/whoami` → empty-state dashboard, zero manual activation.
- **IMPORTANT — auth is mandatory since C2.** There is no dev-mode bypass in `apps/web` itself:
  every route except `/signup` renders behind `AuthProvider`/`useAuth`, which always calls a real
  Supabase Auth endpoint. "Live mode" below MUST point at a real (if local) Supabase project — the
  pre-C2 assumption that you can just run the BFF + `npm run dev` with no auth setup no longer
  holds.
- **Owner:** Miguel Sosa

---

## 1. How to run the manual UAT

### Local emulator (recommended — zero live risk, exercises the full C1–C5 stack)
Bootstraps a throwaway local Postgres + Auth + Storage stack via the Supabase CLI, fully isolated
from the real project. First-time setup creates the `trax_app`/`trax_seed` roles the migrations
expect (a fresh Supabase project — local or live — never has them; see `supabase/README.md`'s
prereqs section) *before* migrations run, since the roles don't exist yet on a brand-new instance:

```bash
# 1. Start with an empty schema first (roles must exist before migrations apply)
mv supabase/migrations supabase/migrations.bak
supabase start
# 2. Bootstrap the two app roles as superuser (one-time per fresh local instance)
docker exec -i supabase_db_aeronta-inventory psql -U postgres <<'SQL'
do $$ begin
  if not exists (select from pg_roles where rolname = 'trax_app') then
    create role trax_app login password 'trax_app_local' nobypassrls;
  end if;
  if not exists (select from pg_roles where rolname = 'trax_seed') then
    create role trax_seed login password 'trax_seed_local' bypassrls;
  end if;
end $$;
SQL
# 3. Restore migrations and apply them now that the roles exist
mv supabase/migrations.bak supabase/migrations
supabase migration up --local
supabase status -o env   # note ANON_KEY, JWT_SECRET, SERVICE_ROLE_KEY, DB_URL for steps below
```

`supabase/config.toml` already carries `[auth.hook.custom_access_token]` pointing at
`public.custom_access_token_hook`, so local logins mint the same `tenant_id`/`tenant_role` claims a
real login gets in production — no separate wiring needed.

```bash
# terminal 1 — BFF against the local Postgres (uvicorn now a real `bff`-extra dependency)
cd services/agent-spine
DATABASE_URL='postgresql://trax_app:trax_app_local@127.0.0.1:54322/postgres' \
AUTH_JWT_SECRET='<JWT_SECRET from supabase status>' \
SUPABASE_URL='http://127.0.0.1:54321' \
SUPABASE_SERVICE_KEY='<SERVICE_ROLE_KEY from supabase status>' \
  uv run --extra bff uvicorn trax_io_spine.bff.asgi:app --port 8001
# /healthz should return {"ok":true,"tenants_cached":0} on a fresh instance — confirms
# C5's dynamic registry mode (not the old single-PLANNER_TENANT boot) is active.

# terminal 2 — frontend, apps/web/.env.local (gitignored):
#   VITE_SUPABASE_URL=http://127.0.0.1:54321
#   VITE_SUPABASE_ANON_KEY=<ANON_KEY from supabase status>
cd apps/web && npm install && npm run dev -- --port 5273
```
Open `http://localhost:5273/#/signup` to exercise C5's headline case — a brand-new signup reaching
a fully working, empty-state product with zero manual activation — or `http://localhost:5273` to
sign in with an existing local user. `supabase stop` tears the whole thing down; nothing here
touches the real project.

### Live mode (real Supabase project — only for a final pre-release smoke, not routine UAT)
```bash
# terminal 1 — start the BFF (from services/agent-spine)
cd services/agent-spine
EXTRACT_DIR=../recommendation-engine/examples/extract_sample \
AUTH_JWT_SECRET=<real project's JWT secret> \
  uv run --extra bff uvicorn trax_io_spine.bff.asgi:app --port 8001

# terminal 2 — apps/web/.env.local pointed at the REAL project's VITE_SUPABASE_URL/ANON_KEY
cd apps/web
npm install            # first time only
npm run dev
# open http://localhost:5173 (VITE_BFF_URL defaults to http://localhost:8001)
```
Requires a real account in the target Supabase project to sign in — there is no seeded/fake client
to reset between runs; reloading refetches from the live BFF. Prefer the local emulator above for
routine UAT; use this only to spot-check against the real project's data/auth right before a
release.

### Docker (full-stack, real eMRO-shaped data, in-memory single-tenant snapshot — pre-C2 auth model)
```bash
docker compose up --build web bff   # repo root, project trax-io-planner
# open http://localhost:8089 (apps/web)
```
Never touches `oracle19c`/MySQL — scoped to this project's compose file only. **Note:** this stack
boots the BFF from a precomputed in-memory snapshot (`PLANNER_SNAPSHOT_DIR`), not `DATABASE_URL` —
useful for exercising the 7 core data views at full network scale, but it predates the Supabase
auth shell and doesn't exercise sign-in, signup, billing, members, or C5's multi-tenant serving.
Use the local emulator above for anything auth-related.

### Automated regression gate (run for every release)
```bash
cd apps/web && npm test && npm run build && npm run lint   # 381 tests + typecheck+build + eslint
```
Full-stack regression (backend the UI depends on):
`cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/bff tests/pg`
(Docker required for `tests/pg`; 399 passed / 1 skipped as of C5).

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
`VALVE-MOD-117`). There is no separate fixed-value reference document to eyeball exact numbers
against — treat the sample extract's live engine output as the source of truth for any given run.

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
| B7 | Click a KPI card header (e.g. "Total shortage") | An in-place drill panel expands full-width below the card row (chevron rotates, `aria-expanded` toggles); opening another card closes the first; Escape closes and returns focus to the card header | DrillableCard/DrillPanel + Overview ▸ drill-panel open/close, single-open invariant, Escape, focus restore |
| B8 | Open the "By ATA chapter" drill (≈48 rows) and type in its **Search** box (e.g. "29") | Rows narrow to displayed-label matches, active sort preserved; a non-matching query shows `No ATA chapter rows match "…"`; small breakdowns (tier/criticality/part-class, 3–5 rows) show **no** search box | BreakdownTable ▸ search input threshold, narrows by displayed label, no-match message |

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

### L. Auth & Signup (added C2, extended C4/C5)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| L1 | Open `/` with no session | Sign-in form ("Sign in to Trax Inventory Optimizer", Email/Password/Sign in) — no authed route content leaks | Login ▸ renders the sign-in form when signed out |
| L2 | Sign in with valid credentials | Session established; tenant resolution runs (`GET /v1/auth/whoami`); on success, the app shell renders | useAuth ▸ resolves tenantStatus to "ready" after a successful whoami |
| L3 | Sign in, then let a background token refresh occur (same user, same tenant) | App shell stays mounted throughout — no flash back to a loading/login state | useAuth ▸ a same-identity TOKEN_REFRESHED does not reset tenant-resolution state (Task 8 round-2 regression guard) |
| L4 | Open `/#/signup` | "Start Your 14-Day Free Trial" wizard: account (email/password) → email-confirm interstitial → org creation → plan (monthly/annual) → checkout redirect | SignupWizard ▸ (4-step flow, `SignupWizard.test.tsx`) |
| L5 | Complete signup through org creation | `whoami` re-resolves and reflects the newly-created tenant **without a page reload** (session refresh picks up the new `tenant_id` claim) | useAuth ▸ an identity-claim change (tenant_id absent→present, same user) re-triggers tenant resolution |
| L6 | A signed-in user with zero tenant memberships | A distinct "No tenant access — contact your administrator" card — never a silent crash, infinite spinner, or forced sign-out loop | Login ▸ renders the no-tenant-access card on a 401 from whoami (does not sign out) |
| L7 | `whoami` fails for a reason OTHER than 401 (network/5xx) | A distinct "couldn't load your workspace" card with a **Retry** button — never presented as if it were the user's fault | Login ▸ renders the tenant-resolution error state with Retry, distinct from no-tenant-access |
| L8 | Sign out | Returns cleanly to the sign-in form; no stale tenant data flashes first | (useAuth's `signOut`/`applySession(null)` path) |

### M. Members & Tenant Switching (`/members`, added C2)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| M1 | Open `/members` as an owner/admin | Member list (user, role, joined date) | Members ▸ renders the member list |
| M2 | Belong to 2+ tenants | A tenant switcher in the header; switching reloads the app scoped to the newly-selected tenant | TenantSwitcher ▸ (switch + reload behavior) |
| M3 | Open `/members` as a `planner` role | Read-only — no invite/role-change controls | Members ▸ hides admin-only controls for a planner role |

### N. Billing (`/billing`, added C4, registry-backed C5)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| N1 | Open `/billing` as the tenant owner | Usage meter (keys used vs. plan quota), current plan, a link to the Stripe Customer Portal | BillingPage ▸ renders usage + plan for the owner role |
| N2 | Open `/billing` as a non-owner (planner/admin) | Sees the same billing data, but management actions (upgrade, portal link) are owner-gated — "Ask an owner" | BillingPage ▸ non-owner sees data without owner-only actions |
| N3 | Usage nears/exceeds quota | Subscription banner + an "Upgrade your plan" CTA surfaces on the Data & Connections upload panel | SubscriptionBanner ▸ (status-bucketed banner); UploadPanel ▸ over-quota CTA |
| N4 | A tenant never pre-warmed at boot (fresh/dynamically-resolved via C5's `TenantRegistry`) opens `/billing` | Loads normally — **not** a 404 (a registry fallback was added in C5's final review after this exact gap was caught) | test_c4_billing_read.py ▸ a never-pre-warmed tenant can reach /billing |

### O. Reports / Business Value Report (`/reports`, added C4/wave 3)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| O1 | Open `/reports` | Hero tiles (projected savings, changes applied/shadowed, keys under management, open pipeline, tier posture), a savings-components breakdown, a governance strip, a forward-look section, a methodology disclosure ("valued N of M portfolio keys") | Reports ▸ renders the BVR hero/breakdown/governance/methodology sections |
| O2 | Click a forward-look part link | Navigates into that part's Part Drill-Down | Reports ▸ forward-look links resolve to Part Drill-Down |
| O3 | "Open printable report" / "Download PDF" | Resolves through the same-origin BFF proxy (`bvr.html`/`bvr.pdf`) | (link construction; see `bvrDocumentUrl`) |

### P. Multi-tenant serving & empty state (added C5)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| P1 | A brand-new tenant with zero uploaded data opens any of the 9 authed views | A clean, honest empty state (zero counts, empty lists) on every one — **never** a crash or a fabricated non-zero value | test_c5_empty_tenant.py ▸ all 7 tenant-scoped BFF surfaces (recommendations/dashboard/forecast/feeds/history/reports.bvr/billing) return concrete empty bodies |
| P2 | Open the Data & Connections upload panel on a brand-new tenant and upload the sample CSV/xlsx batch | Ingest job runs; on success the views populate with real computed data — no redeploy, no manual tenant activation | (C3 upload/ingest/poll flow; see `deploy/aeronta_smoke.py`'s optional ingest stage) |
| P3 | Data & Connections ▸ ingest history, after a scheduled overnight recompute has run | The entry is labeled distinctly from a manual upload (never presented as if a person did it); a "superseded" outcome (a newer upload landed first) renders as a neutral badge, not an error | IngestHistory ▸ distinguishes kind=recompute rows and the superseded outcome |
| P4 | Anonymous (no token) `GET /healthz` | Returns a cached-tenant **count**, never a list of real tenant slugs (no org-existence oracle) | test_app.py ▸ /healthz doesn't leak tenant slugs to an anonymous caller |

---

## 4. Traceability & coverage summary

| Area | Cases | Automated | Manual-only |
|---|---|---|---|
| A App shell & navigation | 5 | 5 | — |
| B Overview | 8 | 8 | — |
| C Part Drill-Down | 4 | 4 | — |
| D Workbench (core loop) | 14 | 14 | — |
| E AI Recommendations | 5 | 5 | — |
| F Forecast & Service Levels | 5 | 5 | — |
| G What-If Scenarios | 13 | 13 | — |
| H Data & Connections | 6 | 6 | — |
| I Provenance invariant | 4 | 3 | I4 (staleTime timing) |
| J Accessibility | 7 | 6 | J7 (contrast) |
| K Edge/errors | 3 | 3 | — |
| L Auth & Signup | 8 | 8 | — |
| M Members & Tenant Switching | 3 | 3 | — |
| N Billing | 4 | 4 | — |
| O Reports / BVR | 3 | 2 | O3 (link construction, low-value to automate further) |
| P Multi-tenant serving & empty state | 4 | 3 | P2 (upload/ingest UI click-through — covered at the API layer by the smoke script, not a Vitest component test) |

**Manual-only items to consider automating later:**
- I4 — real-clock staleTime/freshness-aging spot check.
- J7 — automated color-contrast (axe-core) in light mode; dark mode isn't wired in this app yet.
- O3 — link construction is trivial; not worth a dedicated test beyond what exists.
- P2 — a Playwright e2e case would close this (drive the actual file input + poll), see §5.

Everything else is already covered by the **381 Vitest tests**; keep this table in sync as cases
are added so "run the Vitest suite" remains a true automated proxy for this UAT.

---

## 5. e2e (Playwright) — status

**One spec landed** (`e2e/workbench-accept.spec.ts`, `npm run e2e`): Workbench, route-mocked BFF,
accept a recommendation, row leaves the list. This was verified working end-to-end during the S8
hardening slice.

**Deferred** (future work):
- The other views' e2e coverage (Overview, Part Drill-Down, AI Recommendations, Forecast,
  Scenarios, Data & Connections, Reports, Members, Billing).
- A real signed-in-user e2e trace (today's one spec route-mocks the BFF entirely; a real Supabase
  local-emulator-backed e2e run would additionally exercise auth + whoami end to end).
- A full keyboard-only traversal + screen-reader announcement sweep (J1/J2/J6 today are Vitest
  component-level; a real end-to-end keyboard trace would strengthen confidence further).
- Error/retry paths end-to-end (currently Vitest-level via mocked `fetch`).
- Automated color-contrast (axe-core) in a real browser.
- The self-serve upload → ingest → poll flow (P2) end to end in a real browser (today verified via
  `deploy/aeronta_smoke.py`'s API-level stage, not a UI click-through).

---

## 6. Per-release checklist

1. `npm test` (381 green) · `npm run build` (tsc -b + vite) · `npm run lint` (eslint) clean.
2. Backend regression the UI depends on:
   `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/bff tests/pg`
   (Docker required for `tests/pg`).
3. Best-effort: `npm run e2e` (1 Playwright spec — requires `npx playwright install chromium`
   once).
4. Smoke manually against the **local emulator** (§1) — this is now the primary manual UAT
   environment, not live mode: cases L1–L6 (sign-in/signup/no-tenant-access), A1, B1, D1, D2, G7,
   H1, I1, J6, P1 (empty-state on a fresh tenant).
5. Before an actual live release, additionally run `deploy/aeronta_smoke.py` against the target
   deployment (see its own docstring for the env vars and optional ingest/billing stages).
6. If any UI behavior changed, add/adjust the matching case here **and** its Vitest test in the
   same PR.
