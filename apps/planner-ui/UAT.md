# Planner UI ("Trax IO Review") — UAT Test Plan

**Living document** — run before every release. Each manual case lists the automated test that
already covers it (`Auto` column), so this plan is both a manual UAT checklist and the map for the
automated regression gate. Update it whenever a feature is added or changed.

- **Component:** `apps/planner-ui` (React 18 + TS + Vite, BFF = `trax_io_spine.bff`)
- **Last validated against:** part-context + portfolio-dashboard slice (98 Vitest tests green)
- **Owner:** Miguel Sosa

---

## 1. How to run the manual UAT

### Offline mode (recommended for UAT — deterministic, no backend)
```bash
cd apps/planner-ui
npm install            # first time only
VITE_FAKE=1 npm run dev
# open http://localhost:5173
```
Offline mode uses the in-memory `FakePlannerClient` seeded from `SAMPLE_SEED` + `SAMPLE_HISTORY`
(`src/api/sample.ts`). It mirrors the BFF lifecycle exactly, so every case below is reproducible.
**Reset between runs:** reload the page (state is in-memory; a reload restores the seed).

### Live mode (integration UAT against a running BFF)
```bash
# terminal 1 — start the BFF (from services/agent-spine)
uv run --extra bff uvicorn 'trax_io_spine.bff.app:...'   # host create_planner_app(stores)
# terminal 2
cd apps/planner-ui
VITE_BFF_URL=http://localhost:8000 npm run dev
```
Live values differ from the seed; use the offline seed below as the authoritative expected data.

### Automated regression gate (run for every release)
```bash
cd apps/planner-ui && npm test && npm run build && tsc -b   # 98 tests + typecheck + build
```
Full-stack regression (backend the UI depends on): run the repo-wide suite —
`tools/.../uta.sh` pattern, or per-package `uv run --extra dev pytest` (agent-spine `--extra bff`
covers the BFF the UI calls, now **136 tests** with the `/parts/{pn}/{location}` + `/dashboard`
reads). Last full run: **692 tests green** across 9 packages (pre-dates this slice's BFF growth;
re-run before release).

---

## 2. Seed / fixture reference (offline mode)

**Pending queue** (priority-sorted, highest first):

| # | recommendation_id | Part · Location | Type | Tier | Crit | Priority | Cost impact | Approvable? | Current → Proposed policy |
|---|---|---|---|---|---|---|---|---|---|
| 1 | rec-hyd-yyz | HYD-PUMP-001 · YYZ | Transfer | A | 1 | 45.9 | $8,400 | **Yes** | ROP 6→9, EOQ 10→12, SS 2→4, Max 20→24 |
| 2 | rec-hyd-yow | HYD-PUMP-001 · YOW | Purchase | A | 1 | 38.2 | $5,600 | **Yes** | ROP 4→6, EOQ 6→8, SS 1→2, Max 12→16 |
| 3 | rec-filter-yyz | FILTER-EXP-042 · YYZ | Adjust Min Max | B | 3 | 12.4 | $180 | **No (advisory)** | — (no writable change) |
| 4 | rec-valve-yyz | VALVE-MOD-117 · YYZ | Reduce Stock | C | 4 | 6.1 | -$1,200 | **No (advisory)** | — (no writable change) |

**Seeded writeback history** — `HYD-PUMP-001 · YYZ` already has one prior applied write
**v1** (2026-06-20, `agent-spine`): `ROP 6 · EOQ 10 · SS 2 · Max 20` (old `ROP 5 · EOQ 8 · SS 1 · Max 16`).
This is why HYD-PUMP-001 · YYZ has a populated, revertible history.

**Recording results:** mark each case **Pass / Fail / Blocked**. For a Fail, capture the case ID,
the actual result, a screenshot, and the browser/OS.

---

## 3. Test cases

> Legend — **Auto** column: the automated test that covers the case (`file ▸ test name`), or
> `MANUAL` (real-browser/visual only — not automated), or `TO-AUTOMATE`.

### A. Setup & smoke

| ID | Steps | Expected | Auto |
|---|---|---|---|
| A1 | Start offline mode, open the app | Header "Trax IO Review", "acme · 4 pending", "Agent active" pill, **Pending** tab active, 4 rows | App ▸ loads the priority-sorted queue |
| A2 | Observe row order | Rows ordered by Priority desc: 45.9, 38.2, 12.4, 6.1 | client ▸ returns pending rows priority-desc |
| A3 | Observe tier badges & criticality dots | Tier badges A/A/B/C; left dot color reflects criticality (1=red … 4=green) | QueueTable ▸ renders one row per recommendation with its tier badge |

### B. Pending queue

| ID | Steps | Expected | Auto |
|---|---|---|---|
| B1 | Click a row's part name (e.g. HYD-PUMP-001 · YYZ) | Row highlights as selected; detail panel populates below | App ▸ selecting a row reveals its provenance |
| B2 | Inspect the Approve buttons | Rows 1 & 2 (approvable) Approve enabled; rows 3 & 4 (advisory) Approve **disabled** with tooltip "Advisory recommendation — nothing to write" | QueueTable ▸ disables approve for non-approvable (advisory) rows |
| B3 | Cost-impact formatting | Currency, no decimals; negative shown as -$1,200 | QueueTable (money formatter) |

### C. Detail panel / provenance

| ID | Steps | Expected | Auto |
|---|---|---|---|
| C1 | Select HYD-PUMP-001 · YYZ | "Current → proposed" shows ROP 6→9, EOQ 10→12, SS 2→4, Max 20→24 (proposed in accent color) | DetailPanel ▸ renders the current→proposed diff, reason, and evidence |
| C2 | Read "Why this is queued" | "Tier A — essentiality 1 (flight-safety). Requires planner approval." | DetailPanel ▸ …reason… |
| C3 | Read "Evidence" | Lists "Open Order 3 due 2026-05-04" and "Demand History 14 removals / 24mo" | DetailPanel ▸ …evidence |
| C4 | Provenance id | `prov-7af3` shown top-right of the panel | DetailPanel (render) |
| C5 | Select an advisory row (FILTER-EXP-042) | Panel shows "Advisory — no writable policy change."; Approve disabled | DetailPanel ▸ disables approve for an advisory (no-policy) recommendation |
| C6 | With nothing selected | Panel shows "Select a recommendation to review its provenance." | DetailPanel ▸ shows an empty state when nothing is selected |

### D. Approve / Reject / Defer

| ID | Steps | Expected | Auto |
|---|---|---|---|
| D1 | Approve row 1 (HYD-PUMP-001 · YYZ) via the row's Approve button | Row leaves the queue; header → "acme · 3 pending"; 3 rows remain | App ▸ approving a policy-bearing row removes it from the queue |
| D2 | Select FILTER-EXP-042, choose a reject reason, click Reject | Row removed from Pending; reason recorded (visible later on Decided) | App ▸ rejecting from the detail panel removes the row |
| D3 | Select a row, click Defer | Row removed from Pending (moves to Decided as deferred) | DetailPanel ▸ approve and defer fire their handlers; usePlanner ▸ tabs |
| D4 | Reject reason selector | Options: Wrong for fleet / Wrong essentiality / Bad lead time / Planner override / Other; selected reason is passed | DetailPanel ▸ reject fires onReject with the selected reason |

### E. Bulk approve — "Approve matching" (in the Toolbar)

The bulk action is folded into the single Toolbar: the tier / type filters drive it, and
"Approve matching" bulk-approves the pending rows the server matches.

| ID | Steps | Expected | Auto |
|---|---|---|---|
| E1 | Check **Tier A** in the toolbar, click "Approve matching" | The 2 approvable Tier-A rows are written; banner "Approved 2 recommendations."; header → "acme · 2 pending" | App ▸ bulk-approving Tier A clears the matching approvable rows |
| E2 | With no filter set, click "Approve matching" | Only the approvable rows are approved (advisory skipped) | client ▸ bulk-approve with no filter approves only the approvable rows |
| E3 | Select the **Transfer** type, click Approve matching | Filter carries `types: ["transfer"]`; only matching approvable rows approved | client ▸ bulk-approve with no filter … + Toolbar ▸ selects a type |
| E6 | Engage kill switch, then Approve matching | Blocked — banner "kill switch engaged" (HTTP 423); nothing approved | client ▸ bulk-approve while the kill switch is engaged throws 423; Toolbar disables the button when paused |
| E7 | Switch to Decided tab | The toolbar (and "Approve matching") is **hidden** — decided rows are read-only | App ▸ (Toolbar only on Pending) |

### F. Guards (correctness)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| F1 | Click Approve, then immediately click another Approve before it settles (offline is fast — best seen in live mode or via test) | Only one write fires; action buttons disabled while in flight (busy) | usePlanner ▸ ignores a second action while one is in flight; App ▸ disables approve buttons while a write is in flight |
| F2 | Rapidly select row A then row B | Detail shows B (a slow A response cannot overwrite a newer selection) | usePlanner ▸ a stale getDetail does not overwrite a newer selection |

### G. Writeback history & rollback

| ID | Steps | Expected | Auto |
|---|---|---|---|
| G1 | Select HYD-PUMP-001 · YYZ (Pending) | "Writeback history" section shows **v1** (2026-06-20 · agent-spine, ROP 6 · EOQ 10 · SS 2 · Max 20) | App ▸ surfaces a selected row's writeback history; DetailPanel ▸ renders the writeback history |
| G2 | Click "Roll back last change" | Banner "Rolled back HYD-PUMP-001 · YYZ to the previous policy."; a new revert entry (v2) appears at the top of the timeline | App ▸ surfaces… and rolls it back |
| G3 | Select a part with no prior writes (e.g. VALVE-MOD-117 · YYZ) | "No prior writes for VALVE-MOD-117 · YYZ." | DetailPanel ▸ shows an empty-history note when there are no prior writes |
| G4 | Rollback disabled when nothing revertible | "Roll back last change" disabled with tooltip when the latest write has no known prior value | DetailPanel ▸ disables rollback when the latest write has no known prior value |
| G5 | Approve HYD-PUMP-001 · YYZ, go to Decided, select it | History now shows **v1** (seeded) **+ v2** (your approve, today's date, ROP 9 · EOQ 12 · SS 4 · Max 24); rollback available | App ▸ an approved row surfaces under the Decided tab with its writeback history |

### H. Pending / Decided tabs

| ID | Steps | Expected | Auto |
|---|---|---|---|
| H1 | Approve a row, then click the **Decided** tab | Header → "acme · 1 decided"; the approved row appears with a green **Approved** status badge; **no Approve button** | App ▸ an approved row surfaces under the Decided tab… |
| H2 | Reject a row, then view Decided | The rejected row appears with a **Rejected** badge | usePlanner ▸ tabs (merges approved/rejected/deferred) |
| H3 | Select a decided row | Detail shows provenance + writeback history + rollback, but **no approve/reject/defer** actions | DetailPanel ▸ decided mode hides the approve/reject/defer actions but keeps history + rollback |
| H4 | Decided tab when nothing decided yet | Empty state "No decided recommendations yet." | QueueTable ▸ decided mode has its own empty state |
| H5 | Switch tabs while a row is selected | Selection clears on tab switch | usePlanner ▸ switches between pending and decided rows and clears the selection |

### I. Kill switch

| ID | Steps | Expected | Auto |
|---|---|---|---|
| I1 | Click "Agent active" pill | Pill → "Agent paused" (red); banner "Agent paused — approvals are disabled until you resume." | App ▸ engaging the kill switch shows the banner and disables approve; KillSwitchHeader ▸ shows active/paused |
| I2 | While paused, inspect Approve buttons | All Approve buttons disabled; Reject/Defer remain enabled (they don't write) | App ▸ …disables approve |
| I3 | Click "Agent paused" to resume | Pill → "Agent active"; Approve re-enabled | KillSwitchHeader ▸ shows paused state and resumes on click |

### J. URL routing (react-router HashRouter)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| J1 | Click the **Decided** tab; check the address bar | URL hash becomes `#/decided` | App ▸ clicking a tab updates the URL hash |
| J2 | Open `http://localhost:5173/#/decided` directly (deep link) | App opens on the **Decided** tab | App ▸ deep-links to the Decided tab from the URL hash |
| J3 | Navigate Pending→Decided, then browser **Back** | Returns to the Pending tab | MANUAL (real browser history) |
| J4 | Open `#/` or an unknown hash | Redirects to `#/pending` | App ▸ routing (`*` → /pending) |

### K. Accessibility / keyboard (WCAG)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| K1 | Focus the Pending tab, press **→ / ←** | Focus + selection move between tabs (wraps); only the active tab is in the tab order (roving tabindex) | Tabs ▸ moves to the next/previous tab on arrow keys; Tabs ▸ roving tabindex |
| K2 | Inspect the tabs/panel semantics (devtools) | `role="tablist"` with two `role="tab"`; each tab `aria-controls="queue-tabpanel"`; the queue is `role="tabpanel"` `aria-labelledby` the active tab | Tabs ▸ aria-controls; App ▸ exposes the queue as a tabpanel labelled by the active tab |
| K3 | Tab through the page with the keyboard only | All controls reachable & operable: tabs, row selectors (buttons), Approve, bulk fields, reject select, rollback. Focus ring visible (`:focus-visible`) | QueueTable ▸ the row selector is a keyboard-operable button; partial MANUAL |
| K4 | Screen-reader / a11y tree: a row | Criticality announced as text ("Criticality 1."), not color only; tier as text A/B/C | QueueTable ▸ …exposing criticality as text |
| K5 | Trigger a banner (kill switch / bulk result) | Announced assertively (`role="alert"`); loading state is `role="status"` | MANUAL (SR) + App tests assert role |
| K6 | Color-contrast spot check (light & dark mode) | Text meets WCAG AA contrast; dark mode via `prefers-color-scheme` | MANUAL (visual) |

### L. Edge cases & error handling

| ID | Steps | Expected | Auto |
|---|---|---|---|
| L1 | Approve an advisory row (force via API/live) | BFF returns 409; UI surfaces an error banner, no crash | client ▸ approve on a no-policy rec throws 409; usePlanner (error→banner) |
| L2 | Approve while kill switch engaged | 423; banner "kill switch engaged"; nothing written | client ▸ approve while the kill switch is engaged throws 423 |
| L3 | Empty Pending queue (approve/decide all) | "No pending recommendations. You're all caught up." | QueueTable ▸ shows an empty state when there are no rows |
| L4 | Network/parse failure (live mode, stop BFF) | Generic banner "Something went wrong. Please try again." — no unhandled crash | usePlanner (messageFor non-PlannerError) |
| L5 | Tenant isolation (live, two tenants) | Tenant A never sees or acts on tenant B's recommendations | BFF Python suite ▸ tenant-isolation test |

### M. Ops-console shell (redesign)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| M1 | Observe the app shell | Left **nav rail**: Review (active), Dashboard/Writebacks/Settings disabled ("coming soon"). Toolbar, KPI cards, charts, then the table | NavRail ▸ marks Review current, disables placeholders |
| M2 | Type "valve" in **Search** | Table narrows to VALVE-MOD-117 only; KPI cards/charts unchanged (they summarize the whole queue) | queryView ▸ filterRows searches pn/location; Toolbar ▸ emits search |
| M3 | Check **Tier A** / pick **Type** / pick **AOG risk** | Table filters to matching rows (AND); clearing a control restores rows | queryView ▸ filterRows by tier/type/aogMin; Toolbar ▸ tier/type/aog |
| M4 | Click a sortable column header (e.g. **Cost impact**) | Rows re-sort by that column; click again toggles asc/desc; header shows `aria-sort` | queryView ▸ sortRows; QueueTable ▸ onSort + aria-sort |
| M5 | Read the **KPI cards** | Pending 4 · Net cost impact $12,980 · AOG risk 1 (red) · Tier A to approve 2 | queryView ▸ summarize; SummaryCards |
| M6 | Read the **charts** | "By type" donut (center = 4, legend per type) + "By tier" bars (A 2 · B 1 · C 1) | ChartRow ▸ summarizes by type and tier |
| M7 | Read the table's new columns | AOG badge (High = red, Medium = blue, Low/None muted), Confidence (0.78), criticality dot on the part | QueueTable ▸ renders the AOG badge and confidence |
| M8 | Click **Export** | A CSV of the current (filtered/sorted) view downloads | queryView ▸ toCsv (download is browser-only) |

### N. Part context — enriched columns & part drawer

The queue splits **Part**, **Location**, and **Description** into their own columns, carries
stock-position columns, and selecting a row lazily fetches a `PartContext`
(`GET /v1/tenants/{tenant}/parts/{pn}/{location}`) that populates a part drawer inside `DetailPanel`.

| ID | Steps | Expected | Auto |
|---|---|---|---|
| N0 | Observe the leftmost columns | **Part**, **Location**, and **Description** are separate columns (Part carries the criticality dot + is the clickable selector; Description e.g. "Hydraulic pump"). All three are sortable headers | QueueTable ▸ shows Part, Location and Description as separate columns |
| N1 | Observe the queue's column headers | **On hand** and **Need** columns present alongside Part/Location/Description | QueueTable ▸ shows on-hand and need columns |
| N2 | Read row 1 (HYD-PUMP-001 · YYZ) | On hand **4**, Need (shortage) **3** — matches the seed's `current_stock`/`shortage_quantity` | QueueTable ▸ renders each row's current stock and rounded shortage quantity |
| N3 | Select HYD-PUMP-001 · YYZ | Part drawer appears in the detail panel: headline "Hydraulic pump", ATA 29, part class rotable, criticality tier 1 | DetailPanel ▸ renders the part context header, stock strip, and demand trend when present |
| N4 | Read the stock strip in the drawer | On-hand 4 · Serviceable 3 · In-repair 1 · Need 3 · demand **0.42**/90d (sub-unit demand shown with real precision, not rounded to 0) | DetailPanel ▸ renders the part context header, stock strip, and demand trend when present |
| N5 | Read the lead-time line | Promised 21 days · realized mean 26.5 days (n=6 observations) | DetailPanel ▸ renders the part context header, stock strip, and demand trend when present |
| N6 | Read open orders | "1 open order" / total open qty 3 (PO-4471, Trax Spares Co., due 2026-08-04) | DetailPanel ▸ renders the part context header, stock strip, and demand trend when present |
| N7 | Observe the demand-trend chart | Inline SVG line/bar chart renders 12 months of demand (no external chart library) | DetailPanel ▸ renders the part context header, stock strip, and demand trend when present; DemandTrend ▸ (component tests) |
| N8 | Select a row before the part-context fetch resolves / on fetch failure | Drawer section is simply absent — no crash, no placeholder flash | DetailPanel ▸ does not render part-context sections when partContext is absent |

### O. Dashboard (`#/dashboard`)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| O1 | Click **Dashboard** in the nav rail | Nav item is now live (no longer "coming soon"); URL hash becomes `#/dashboard` | NavRail ▸ marks Review current, disables placeholders (Dashboard now live) |
| O2 | Read the KPI tiles | Parts **4** · Total on-hand **49** (value $137,200) · Total shortage **4** · Projected demand **2.73** · AOG exposure **1** · Open recommendations **4** · Net cost impact **$12,980** | DashboardView ▸ renders portfolio KPIs and top shortages |
| O3 | Read the breakdown cards | By-criticality / by-ATA / by-part-class / by-tier bar cards each show 3 buckets with count/on-hand/shortage | DashboardView ▸ renders portfolio KPIs and top shortages |
| O4 | Read the top-shortages table | 2 rows, sorted by shortage desc: HYD-PUMP-001 · YYZ (shortage 3, projected demand **0.42**) then HYD-PUMP-001 · YOW (shortage 1, projected demand **0.31**) | DashboardView ▸ renders portfolio KPIs and top shortages |
| O5 | Open the Dashboard before the fetch resolves | Empty-friendly placeholder state, no crash | DashboardView ▸ renders an empty-friendly state before the fetch resolves |
| O6 | Fetch failure (stop BFF / simulate 500) | Handled gracefully — no unhandled exception, view stays usable | DashboardView ▸ handles a failed fetch without throwing |

### P. Reports — Business Value Report (`#/reports`)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| P1 | Click **Reports** in the nav rail | Item is live; URL hash becomes `#/reports`; view header "Business Value Report" with an **All figures projected** badge | ReportsView ▸ renders the projected hero tiles and the applied/shadowed split |
| P2 | Read the hero tiles | Total projected · Changes applied · Changes shadowed · Keys under management · Open pipeline (fake seed: $51.39 / 1 / 0 / 4 / $1,250.00) | ReportsView ▸ renders the projected hero tiles and the applied/shadowed split |
| P3 | Read the savings section | "N of M changes valued" + applied/shadowed split + the three components (holding / ordering / stockout-risk) with $ amounts | ReportsView ▸ renders the projected hero tiles and the applied/shadowed split |
| P4 | Read the governance strip | Recommendations total · approval rate % · override rate % · rollbacks | ReportsView ▸ shows governance numbers from the report |
| P5 | Click **Open printable report** / **Download PDF** | New tab opens the BFF's `…/reports/bvr.html` printable document; PDF link serves `…/bvr.pdf` (renders inline in most browsers) | ReportsView ▸ links to the printable HTML and the PDF |
| P6 | Fetch failure (stop BFF / simulate 500) | `role="alert"` "Couldn't load the report: …", view stays usable | ReportsView ▸ handles a failed fetch without throwing |
| P7 | Live loop: approve a rec (Review), reopen Reports | Savings/governance reflect the approval (report cache invalidates server-side; verified live: applied went $0.00 → −$0.06 after one approve) | (manual — server-side invalidation covered by BFF test_reports.py) |

---

## 4. Traceability & coverage summary

| Area | Cases | Automated | Manual-only |
|---|---|---|---|
| A Setup/smoke | 3 | 3 | — |
| B Pending queue | 3 | 3 | — |
| C Detail/provenance | 6 | 6 | — |
| D Approve/Reject/Defer | 4 | 4 | — |
| E Bulk approve (Approve matching) | 5 | 5 | — |
| F Guards | 2 | 2 | — |
| G History/rollback | 5 | 5 | — |
| H Pending/Decided tabs | 5 | 5 | — |
| I Kill switch | 3 | 3 | — |
| J Routing | 4 | 3 | J3 (browser back/forward) |
| K Accessibility | 6 | 3 | K3 (full keyboard sweep), K5 (SR), K6 (contrast) |
| L Edge/errors | 5 | 5 | — |
| M Ops-console (search/filter/sort/cards/charts/export) | 8 | 8 | M8 export download is browser-only (logic tested) |
| N Part context (columns + drawer) | 9 | 9 | — |
| O Dashboard | 6 | 6 | — |
| P Reports (BVR) | 7 | 6 | P7 (live approve→report loop; server side automated) |

**Manual-only items to consider automating later** (Playwright E2E in a real browser):
- J3 — browser Back/Forward between tab routes.
- K3/K5 — end-to-end keyboard traversal + screen-reader announcements (axe-core + Playwright).
- K6 — automated color-contrast (axe-core) in light & dark mode.

Everything else is already covered by the 110 Vitest tests; keep this table in sync as cases are
added so "run the Vitest suite" remains a true automated proxy for this UAT.

---

## 5. Per-release checklist

1. `npm test` (111 green) · `npm run build` · `tsc -b` clean.
2. Backend regression the UI depends on: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest` (245 green, 4 skipped incl. the env-gated WeasyPrint test — BFF + agent-spine, incl. `/parts` + `/dashboard` + the BVR reports surface), plus the repo-wide suite if backend changed.
3. Smoke the offline build manually: cases A1, D1, E1, G2, H1, J2, K1, N3, O2, P1 (the critical path).
4. If any UI behavior changed, add/adjust the matching case here **and** its Vitest test in the same PR.
