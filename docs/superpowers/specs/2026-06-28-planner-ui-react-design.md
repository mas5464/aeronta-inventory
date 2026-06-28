# #7 Planner UI — React Frontend (core approval loop) — Design

**Date:** 2026-06-28
**Status:** Proposed
**Sub-project:** #7 Planner UI "Trax IO Review" (React frontend; consumes the BFF shipped in [ADR-0011](../../adr/2026-06-28-0011-planner-ui-bff.md))
**Authoritative inputs:** [BFF design](2026-06-28-planner-ui-bff-design.md) · the live BFF (`trax_io_spine.bff`) · [#7 sub-plan](../../plans/2026-04-14-planner-ui-plan.md)

## 1. Context

The BFF (ADR-0011) exposes the approval queue, provenance, actions, history/rollback, and the kill switch over HTTP. This slice builds the **first frontend in the repo** — a React app rendering the **core approval loop**: the priority-sorted queue, a provenance detail panel, approve/reject/defer, and the kill-switch header. It is the visible payoff of the #7 work.

**Greenfield + iCloud constraint.** The repo lives in iCloud Drive, where a `node_modules` tree (tens of thousands of files) would be mangled into `· 2` conflict copies. So: **build and test in the scratchpad (outside iCloud), commit source-only** to `apps/planner-ui/` (`node_modules`/`dist` gitignored — they never enter the repo, as with any JS project). The committed artifact is verified source + lockfile; `npm install && npm test` reproduces it.

**Stack (probed, node-20.17-safe):** Vite 5 · React 18 · TypeScript 5 · Vitest 2 + React Testing Library + jsdom · **CSS Modules** (Vite-native, zero extra deps). No router, no data-fetching lib, no component lib — minimal dep tree.

## 2. Scope

**In scope:**
1. **Typed API client** mirroring the BFF models (`QueueRow`, `RecommendationDetail`, `ActionResult`, `KillSwitchState`, `RejectReason`, `TaskStatus`) — a `PlannerClient` interface, an `HttpPlannerClient` (fetch over a base URL), and a `FakePlannerClient` (in-memory, for tests + offline dev).
2. **Queue table** — priority-desc rows, tier-colored badges (A/B/C → amber/blue/green), a criticality dot, row selection, and a per-row approve button.
3. **Provenance detail panel** — for the selected recommendation: `current → proposed` policy diff (rop/eoq/safety_stock/max_stock), the "why queued" guardrail reason, and supporting evidence; an action bar (approve / reject-with-reason / defer).
4. **Kill-switch header** — title + tenant + a toggle reflecting `engaged`; while engaged, approve/bulk actions are blocked in the UI with a clear banner.
5. **App composition + state** — load the queue, select a row, run actions (approve/reject/defer remove the row from the pending queue); a small `usePlanner` hook over the client (loading/error states).
6. **Vitest + RTL tests** for every component + an integration test of the loop, all against the `FakePlannerClient`.

**Deferred (tracked in ROADMAP):** bulk-approve filter-builder UI; the history/rollback timeline view; the weekly Tier-C digest; settings (autonomy-bands / service-level); routing/tabs beyond the single view; SSE real-time; auth/SSO; real eMRO embedding; styling polish beyond the flat baseline.

**Non-goals:** changing the BFF; persistence; i18n; accessibility beyond sensible roles/labels (full WCAG audit deferred).

## 3. Architecture

Committed at `apps/planner-ui/` (developed/tested in the scratchpad):

```
apps/planner-ui/
  package.json        # pinned stack (the probed versions); scripts: dev, build, test
  vite.config.ts      # react plugin + vitest (jsdom, globals, setupTests)
  tsconfig.json
  .gitignore          # node_modules, dist
  src/
    main.tsx          # mounts <App> (real HttpPlannerClient from VITE_BFF_URL)
    App.tsx           # composition + the usePlanner hook
    api/
      types.ts        # TS mirrors of the BFF wire models
      client.ts       # PlannerClient interface, HttpPlannerClient, FakePlannerClient
    hooks/usePlanner.ts  # queue + selection + actions over a PlannerClient
    components/
      KillSwitchHeader.tsx + .module.css
      QueueTable.tsx + .module.css
      DetailPanel.tsx + .module.css
    styles/tokens.css  # flat light/dark CSS variables (surfaces, text, tier colors)
    setupTests.ts      # @testing-library/jest-dom
  tests/ (co-located *.test.tsx next to components)
```

### 3.1 API client (`api/`)

- `types.ts` — `TaskStatus`, `RejectReason` (string unions), `AutonomyTier` (1|2|3), `PolicyView{rop,eoq,safety_stock,max_stock}`, `EvidenceView{kind,ref_id,detail,as_of}`, `QueueRow`, `RecommendationDetail`, `ActionResult`, `KillSwitchState` — exact field names from the BFF JSON.
- `PlannerClient` interface: `getQueue(tenant) → Promise<QueueRow[]>`, `getDetail(tenant, id) → Promise<RecommendationDetail>`, `approve(tenant, id) → Promise<ActionResult>`, `reject(tenant, id, reason, detail?) → Promise<ActionResult>`, `defer(tenant, id) → Promise<ActionResult>`, `getKillSwitch(tenant) → Promise<KillSwitchState>`, `setKillSwitch(tenant, engaged) → Promise<KillSwitchState>`.
- `HttpPlannerClient(baseUrl)` — `fetch` against `/v1/tenants/{tenant}/...`; maps non-2xx to a typed `PlannerError{status, message}` (so the UI can distinguish 423 kill-switch).
- `FakePlannerClient(seed)` — in-memory queue + kill-switch; `approve` removes the row + flips status; honors kill-switch (throws `PlannerError{status:423}`); used by all tests and offline `npm run dev` (a `VITE_FAKE=1` flag mounts it).

### 3.2 Components

- `KillSwitchHeader({state, onToggle})` — renders the title/tenant + a toggle; `engaged` shows "Agent paused" + a danger tint; calls `onToggle(!engaged)`.
- `QueueTable({rows, selectedId, onSelect, onApprove, disabled})` — a table sorted as received (BFF is priority-desc); each row: criticality dot, `pn · location`, type, tier badge, priority, approve button (disabled when `disabled`/kill-switch). Clicking a row calls `onSelect(id)`.
- `DetailPanel({detail, onApprove, onReject, onDefer, disabled})` — the 2-column provenance (current→proposed diff + why-queued/evidence) + the action bar; reject opens a reason selector. `null` detail → an empty-state hint.
- `App` — uses `usePlanner(client, tenant)`: holds `rows`, `selectedId`, `detail`, `killSwitch`, `loading`, `error`; actions refresh the queue + clear selection. While `killSwitch.engaged`, a banner shows and approve actions are blocked.

## 4. Testing strategy (Vitest + RTL, against `FakePlannerClient`)

- **client** — `FakePlannerClient.approve` removes the row + returns `approved`; throws `423` while engaged; `HttpPlannerClient` maps a mocked 423 fetch to `PlannerError{status:423}` and a 200 to the parsed body.
- **KillSwitchHeader** — shows "Agent active/paused" per state; toggle fires `onToggle` with the negated value.
- **QueueTable** — renders one row per entry in order; tier badge text A/B/C; clicking a row fires `onSelect`; approve button fires `onApprove`; `disabled` hides/disables approve.
- **DetailPanel** — renders the current→proposed diffs + why-queued + evidence; approve/defer fire handlers; reject requires a reason then fires `onReject(reason)`; `null` → empty state.
- **App integration** (FakePlannerClient seeded like the real sample: 4 rows, 2 policy-bearing) — loads the queue; selecting a row loads detail; approving a policy-bearing row removes it (queue shrinks); engaging the kill switch shows the banner and blocks approve; rejecting records and removes the row.

## 5. Build & verify

- Develop + run `npm test` (Vitest) and `npm run build` (tsc + vite) in the scratchpad until green.
- Copy source-only into `apps/planner-ui/` (exclude `node_modules`/`dist`); commit. The repo holds source + `package-lock.json`; `cd apps/planner-ui && npm install && npm test` reproduces. CLAUDE.md documents this + the iCloud caveat.
- A short README in `apps/planner-ui/` documents `npm run dev` (with `VITE_FAKE=1` for the offline FakePlannerClient, or `VITE_BFF_URL` against a running BFF).

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| iCloud mangles `node_modules` | Build in the scratchpad; commit source-only; `node_modules` gitignored. |
| Vite 8 needs node ≥20.19 (we're 20.17) | Pinned Vite 5 / Vitest 2 — probed green. |
| Repo can't run tests without `npm install` | Normal for JS; tests verified in scratchpad pre-commit; CLAUDE.md notes the install step. |
| Scope creep into bulk/history/settings | Explicitly deferred; the BFF endpoints exist, so they're additive later. |

## 7. Deliverables

- `apps/planner-ui/` (source + configs + tests + README), tests green in the scratchpad, `tsc`/`vite build` clean.
- ADR-0012 (React frontend stack + the scratchpad-build/source-commit pattern for iCloud).
- CLAUDE.md `apps/planner-ui` row (build/test commands + iCloud note); ROADMAP #7 React-frontend done; TASKS.md.
