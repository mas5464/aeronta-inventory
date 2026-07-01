# Real eMRO Pipeline — Wave 3 Plan (scale to all planning-active keys)

> Execute via superpowers:subagent-driven-development. Spec:
> docs/superpowers/specs/2026-07-01-real-emro-full-network-pipeline-design.md

**Goal:** scale the deployed pipeline from the YYZ-500 slice to **all ~62,492
planning-active `(PN, planning-loc)` keys network-wide**, viewable in the Planner
UI. Three architecture fixes make 62K runnable, then a full extract + deploy.

**De-risk order:** build + test every fix at the *current* YYZ-500 scale first;
flip to the full 62K only in the final ops task (W3-6).

## Global constraints
- Oracle `localhost:1521/LOCAL` (`ODB`) is read-only; never touch the `oracle`/MySQL
  containers; Docker scoped to `trax-io-planner`; single sequential builds.
- `pool_by_part=True` + statistical projector remain on for real runs.
- No secrets committed; real extract data stays gitignored.
- Every existing test stays green (BFF, reco, extract, UI). Cross-package uv edits
  need `uv sync --extra … --reinstall` (not `--reinstall-package`).
- Persisted artifacts must be **JSON**, not pickle — the offline host is Python 3.14,
  the container is 3.12; pickle would break across versions.

## Task W3-1 — Indexed dashboard aggregation (BFF)
**File:** `services/agent-spine/src/trax_io_spine/bff/store.py` (`dashboard()`), tests.
Today `dashboard()` scans `self.keys` and does an O(n) lookup into `_entries` per
key → ~O(keys²); at 62K that's billions of ops. Build a dict index
`{(pn, location): _Entry}` once (or reuse `_entries` keyed access) so the
aggregation is O(keys + entries). Behavior/output identical — only complexity
changes. Add a test asserting the dashboard output is unchanged vs the current
implementation on the sample, and (if practical) a timing/shape sanity check.

## Task W3-2 — Server-side pagination on the queue endpoint (BFF)
**Files:** `bff/app.py` (the `GET …/recommendations` route), `bff/models.py`
(a paged response model), `bff/store.py` (a paged query method), tests.
- Add `limit` (default e.g. 50, max e.g. 200) + `offset` query params and return a
  paged envelope: `{ items: QueueRow[], total: int, limit, offset }` (add a
  `PagedQueue` model mirroring the `_Base` frozen/extra-forbid convention).
- Server-side **sort** by `priority_score` desc (stable) so paging is coherent;
  keep the existing `status` filter. Document that free-text search/tier/type
  filtering stay client-side over the loaded page for now (or add server-side
  `search`/`tier`/`type` params if cheap — decide during implementation; if
  client-only, `log`/comment the limitation).
- Preserve a non-paged path or a large default so existing callers/tests don't
  break; update tests to the new envelope where they assert the list.

## Task W3-3 — Paginated QueueTable (UI)
**Files:** `apps/planner-ui/src/api/{types,client}.ts`, `hooks/usePlanner.ts`,
`components/QueueTable.tsx` + a pager control, `lib/queryView.ts` (scope note), tests.
- `PlannerClient.getQueue` returns the paged envelope; `usePlanner` tracks
  `page`/`limit`/`total` and exposes `nextPage`/`prevPage` (or a page bar).
- `QueueTable` renders the current page + a pager (Prev/Next + "X–Y of N").
- Sort is server-driven (priority); search/tier/type filter operate on the loaded
  page (documented) OR are wired to server params if W3-2 added them. Keep the
  Decided tab working. Update Vitest tests to the paged shape; keep all green.

## Task W3-4 — Offline-precomputed seed (batch tool + BFF load path)
**Files:** new `services/agent-spine/src/trax_io_spine/bff/precompute.py` (a CLI/entry),
`bff/store.py` (a `from_snapshot` classmethod), tests.
- **Batch** (`trax-io-precompute` or a module `python -m …`): given `extract_dir`,
  `tenant`, `now`, `--pool-by-part`, `--projector statistical`, run
  `build_stores_from_extract` → `RecommendationService(...).run(keys)` → write the
  resulting `recommendations` to `recs.json` (pydantic `model_dump`, a JSON array),
  plus a small `meta.json` (tenant, now, counts). This is the slow step, run once.
- **BFF `PlannerStore.from_snapshot(*, tenant_id, extract_dir, recs_file, now, pool_by_part)`:**
  rebuild `fs`/`keys` from the extract (fast — no reco), load `recs.json`, run
  guardrails, populate `_entries`. Skips the expensive reco at boot. `fs` stays
  available for `/parts` + dashboard.
- `asgi.py`: if `PLANNER_RECS_FILE` env is set, seed via `from_snapshot`; else the
  current `from_extract` path (unchanged default).
- Tests: precompute over the sample → recs.json; `from_snapshot` reproduces the
  same queue/entries as `from_extract` on the sample (equivalence). Keep default
  path unchanged.

## Task W3-5 — Planning-active network-wide extract scope (extract)
**Files:** `tools/nightly-extract/src/trax_io_extract/{scope.py,cli.py}`, tests.
- Add a scope mode that selects **all planning-active PNs network-wide** (not one
  station): a flag `--scope-planning-active` (no location) whose `resolve_scope`
  variant returns the set of PNs with `(NVL(REORDER_LEVEL,0)>0 OR NVL(MAXIMUM_STOCK,0)>0)`
  across all locations, capped by `--scope-max-parts` (default high, e.g. 100000).
  For the poolable `part`-scoped domains this pulls those PNs network-wide; the
  `part_location` domains (policy, part_location) stay filtered to the planning
  keys (all planning-active rows). **Chunk the IN-list**: >1000 PNs must be chunked
  (the W1-2 single-IN-list cap is lifted here) — implement OR-of-IN-chunks or a
  `SELECT column_value FROM TABLE(:arr)` bind array. Tests for the chunking + the
  new scope resolution SQL.

## Task W3-6 — Full 62K run + deploy + verify (OPS — controller-run)
1. Measure: with the network-wide planning-active scope, how many PNs/keys and how
   large are the stock/demand pulls (read-only). Window demand appropriately
   (extract-date 2024-04, ~120mo as in Wave 1). If the full 62K is too large for a
   first pass, cap `--scope-max-parts` to a large runnable N (e.g. 10–20K) and note
   it — `log` the cap, don't silently truncate.
2. Run the full extract → `trax-io-precompute` (pooled, statistical) → `recs.json`.
   Measure runtime + memory.
3. Point the Docker BFF at the full extract + `recs.json` (via `PLANNER_RECS_FILE`);
   single sequential rebuild + redeploy; verify the dashboard shows the full portfolio
   and the UI pages through real keys. Confirm boot is fast (no reco at boot).

## Done when
The deployed UI pages through the full planning-active portfolio (or the largest
runnable N, logged), dashboard KPIs are O(keys), boot skips the reco (loads
precomputed recs), all suites green. Update trackers (ROADMAP/TASKS/CLAUDE/UAT) +
push. Then final whole-branch review of the eMRO-pipeline branch.
