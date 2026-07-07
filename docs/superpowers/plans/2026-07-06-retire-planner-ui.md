# Retire `apps/planner-ui` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the redundant `apps/planner-ui` frontend now that `apps/web` is at parity, leaving one frontend over the unchanged shared BFF.

**Architecture:** A deletion + documentation slice. Delete the app, its Docker service, and its dev-launch configs; erase the retired frontend from the working tree's current docs (git retains history); keep the surviving BFF backend and its design docs entirely untouched. No production code behavior changes anywhere — only deletions, doc edits, and comment/test-name rewrites.

**Tech Stack:** React/Vite (deleted app), Docker Compose + nginx (stack), Markdown docs, pandoc (guide `.docx` recompile), `uv`/pytest (agent-spine verify), npm/Vitest (apps/web verify).

## Global Constraints

- **This retires the FRONTEND only. The BFF backend is NOT touched.** The BFF at `services/agent-spine/src/trax_io_spine/bff/` is surviving shared code that `apps/web` depends on; its `PlannerStore` / `create_planner_app()` / `trax-io-spine` CLI / `PLANNER_SNAPSHOT_DIR` / `PLANNER_TENANT` identity stays exactly as-is. Never rename a `Planner*` symbol or `PLANNER_*` env var. A post-slice `grep -i planner` SHOULD still match BFF code + BFF docs — that is correct.
- **No behavior changes.** Every code edit in this slice is a comment, docstring, or test *name* — never logic. The `apps/web` (288 Vitest) and `agent-spine` (`--extra bff --extra bvr`) suites must stay green with no assertion changes (one test rename aside).
- **Git preserves history.** "Erase all trace" = the current working tree's docs no longer surface the retired frontend. Never rewrite git history.
- **ADRs are the erase-all-trace exception** — ADR-0011/0012 are kept; 0012 gains a "Superseded by ADR-0014" note; 0014 is new.
- **Keep, don't delete, surviving-component docs:** the BFF and part-context-dashboard spec/plan pairs document living endpoints — reframe prose, keep the files.
- **Docker is scoped to this project only** (`docker compose` under repo root, project `trax-io-planner`). Never touch `oracle19c`/MySQL or other projects' containers; never prune.
- Source of truth for classification is a **content grep**, not filenames (e.g. the confidence-hero/turbofan slice docs are planner-ui UI work without "planner" in the filename).
- Spec: `docs/superpowers/specs/2026-07-06-retire-planner-ui-design.md`.

### Scope amendment (2026-07-06, after Task 7 — user chose "Reframe #7 → apps/web")

A comprehensive all-spellings sweep after Task 7 showed the retirement footprint is broader than the initial grep captured. The user decided to reframe the **authoritative docs** too. Three distinct things wear the name — treat them differently EVERYWHERE in Tasks 8-10:
1. **`apps/planner-ui`** — the retired React app. Erase operational refs (paths, `:8088`, run commands, dead doc links).
2. **"Planner-UI BFF" / `PlannerStore` / `create_planner_app` / `PLANNER_*`** — the SURVIVING backend. NEVER rename/scrub; it is correct that grep still finds it.
3. **"Planner UI" = sub-project #7 / "Trax IO Review"** — the review-surface *concept*, historically implemented by `apps/planner-ui`, **now implemented by `apps/web`**. Under the user's decision, reframe these frontend references to `apps/web` (sub-project #7's frontend is now `apps/web`).
   - **CRITICAL exception:** the **external eMRO Planner UI** (the eMRO product's own planner-facing UI, e.g. diagram nodes "Planner / Operator (via eMRO Planner UI)", "eMROUI", the target-stack "Writeback: … + Planner UI module") is a DIFFERENT system and is NOT sub-project #7's frontend — **leave those untouched.** When a reference is ambiguous, do NOT reframe it; flag it for the controller.
   - Real historical citations already adjudicated keep-justified in Task 4 (dense `file.py:LINE` / commit-message records in `customer-testing-gap-remediation`, `bvr-pipeline-v1-local`) stay as-is — reframing them would falsify history.

New tasks added below: Task 8 gains `apps/web/UAT.md`; Task 9 (foundational prose reframe); Task 10 (architecture diagrams).

---

### Task 1: Delete the app + Docker service + launch configs; verify the surviving stack

**Files:**
- Delete: `apps/planner-ui/` (entire directory)
- Modify: `docker-compose.yml` (remove the `ui:` service + fix header comment)
- Modify: `.claude/launch.json` (remove `planner-ui-dev` + `planner-ui-fake`)

**Interfaces:**
- Produces: a repo with a single frontend service (`web` on `:8089`) over `bff`; `:8088` freed.

- [ ] **Step 1: Delete the app directory**

```bash
cd "$(git rev-parse --show-toplevel)"
git rm -r apps/planner-ui
```
Expected: `git rm` stages the deletion of the tracked files (note: `node_modules`/`dist` are gitignored, so `git rm -r` handles tracked files; a following `rm -rf apps/planner-ui` removes any untracked remnants — run it to fully clear the 147 MB).
```bash
rm -rf apps/planner-ui
```

- [ ] **Step 2: Remove the `ui:` service from `docker-compose.yml`**

Delete the entire `ui:` service block (the one building `./apps/planner-ui`, container `trax-io-planner-ui`, ports `8088:80`). Keep `bff` and `web` untouched. Update the file's top comment block from the Planner-UI framing to:
```yaml
# Trax IO — local full-stack test deployment (web UI + BFF).
# Scoped to this project only. Does NOT touch any other containers.
#   docker compose up --build         # build + run
#   open http://localhost:8089         # the Trax Inventory Optimizer web UI
#   docker compose down                # stop + remove
name: trax-io-planner
```
(Leave the project `name: trax-io-planner` as-is — renaming it is churn with no benefit and would orphan any running containers.) Also remove the now-inaccurate `# 8088 is planner-ui,` clause inside the `web` service's port comment, leaving the eMRO/mxpp note.

- [ ] **Step 3: Remove the planner-ui launch configs**

In `.claude/launch.json`, delete the `planner-ui-dev` and `planner-ui-fake` configuration objects from the `configurations` array. Keep `web-dev`. Ensure the JSON stays valid (no trailing comma).

- [ ] **Step 4: Verify the compose file parses without the `ui` service**

```bash
cd "$(git rev-parse --show-toplevel)"
docker compose config >/dev/null && echo "COMPOSE OK"
docker compose config --services
```
Expected: `COMPOSE OK`; services listed are exactly `bff` and `web` (no `ui`).

- [ ] **Step 5: Verify the surviving stack boots and serves**

```bash
docker compose up --build -d bff web
# wait for bff healthy, then:
curl -sf -o /dev/null -w "web:%{http_code}\n" http://localhost:8089/
curl -sf -o /dev/null -w "proxy:%{http_code}\n" http://localhost:8089/v1/tenants/acme/killswitch
docker compose down
```
Expected: `web:200` and `proxy:200` (nginx serves the web app and proxies `/v1` to the BFF). If the BFF snapshot dir is unavailable in the environment, note it and fall back to `docker compose config`-only verification (Step 4) — do not fabricate a boot result.

- [ ] **Step 6: Confirm the JSON is valid**

```bash
python3 -c "import json,sys; json.load(open('.claude/launch.json')); print('launch.json OK')"
```
Expected: `launch.json OK`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: delete apps/planner-ui app, Docker ui service, and dev launch configs"
```

---

### Task 2: New ADR-0014 (retirement) + supersede note on ADR-0012

**Files:**
- Create: `docs/adr/2026-07-06-0014-retire-planner-ui-frontend.md`
- Modify: `docs/adr/2026-06-28-0012-planner-ui-react-frontend.md`

**Interfaces:**
- Produces: ADR-0014 (referenced by `CLAUDE.md` in Task 6).

- [ ] **Step 1: Read the two existing planner-ui ADRs for format**

```bash
sed -n '1,40p' docs/adr/2026-06-28-0012-planner-ui-react-frontend.md
sed -n '1,20p' docs/adr/2026-06-28-0011-planner-ui-bff.md
```
Match the header/status/format convention they use (title line, Status, Context, Decision, Consequences).

- [ ] **Step 2: Write ADR-0014**

Create `docs/adr/2026-07-06-0014-retire-planner-ui-frontend.md` following the observed ADR format. Content must cover:
- **Status:** Accepted (2026-07-06).
- **Context:** `apps/web` reached full parity with `apps/planner-ui` across four merged waves (CSV export, writeback history+rollback, Reports/BVR, dark/light theme). Two frontends over one BFF is redundant maintenance.
- **Decision:** Retire the `apps/planner-ui` frontend. Keep the shared BFF backend (unchanged, including its `Planner*` naming) and `apps/web` as the single frontend.
- **Consequences:** one frontend to maintain; host `:8088` freed; the BFF and its HTTP contract are unaffected; planner-ui's design record remains in git history and in ADR-0012 (now superseded); the BFF's internal `Planner*` naming is intentionally retained as surviving-code identity.
- A line noting this supersedes ADR-0012.

- [ ] **Step 3: Add the supersede note to ADR-0012**

Near the top of `docs/adr/2026-06-28-0012-planner-ui-react-frontend.md` (right after the title / in its Status line), add: `Status: Superseded by [ADR-0014](2026-07-06-0014-retire-planner-ui-frontend.md) (2026-07-06) — the planner-ui frontend was retired after apps/web reached parity.` Leave the rest of the body intact as the historical record.

- [ ] **Step 4: Verify links resolve**

```bash
test -f docs/adr/2026-07-06-0014-retire-planner-ui-frontend.md && echo "ADR-0014 exists"
grep -n "0014" docs/adr/2026-06-28-0012-planner-ui-react-frontend.md
```
Expected: `ADR-0014 exists`; the supersede line prints.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/
git commit -m "docs(adr): add ADR-0014 retire planner-ui frontend; mark ADR-0012 superseded"
```

---

### Task 3: Delete the frontend-only UI-slice specs/plans

**Files:**
- Delete (plans): `docs/superpowers/plans/2026-07-02-planner-ui-drawer-bulk-contrast.md`, `2026-07-03-planner-ui-confidence-rationale.md`, `2026-07-03-planner-ui-dark-theme.md`, `2026-07-03-planner-ui-table-badge-conventions.md`, `2026-07-04-planner-ui-confidence-hero-refinement.md`, `2026-07-05-confidence-hero-turbofan-icon.md`
- Delete (specs): the `-design.md` counterpart of each of the above in `docs/superpowers/specs/`, **plus** `docs/superpowers/specs/2026-06-28-planner-ui-react-design.md`
- **Do NOT delete:** `*planner-ui-bff*`, `*part-context-dashboard*` (surviving-component docs — Task 4), `new-web-ui-build-plan`, `apps-web-*`, or any backend doc.

**Interfaces:**
- Consumes: the classification rule from the spec.
- Produces: a docs tree with no frontend-only planner-ui slice docs.

- [ ] **Step 1: Re-grep and classify before deleting (verify the delete list)**

```bash
grep -rliI "planner-ui\|planner_ui\|Planner UI" docs/superpowers/plans docs/superpowers/specs | sort
```
For each hit, confirm it is frontend-only (delete) vs surviving-component (BFF/part-context — keep) vs incidental-mention (keep, scrubbed in Task 5). If a file references only planner-ui UI features (drawer, confidence hero, dark theme, table/badge, turbofan icon, react frontend), it is in the delete set. Report any file whose classification is unclear rather than guessing.

- [ ] **Step 2: Delete the frontend-only slice docs**

```bash
cd "$(git rev-parse --show-toplevel)"
git rm \
  docs/superpowers/plans/2026-07-02-planner-ui-drawer-bulk-contrast.md \
  docs/superpowers/plans/2026-07-03-planner-ui-confidence-rationale.md \
  docs/superpowers/plans/2026-07-03-planner-ui-dark-theme.md \
  docs/superpowers/plans/2026-07-03-planner-ui-table-badge-conventions.md \
  docs/superpowers/plans/2026-07-04-planner-ui-confidence-hero-refinement.md \
  docs/superpowers/plans/2026-07-05-confidence-hero-turbofan-icon.md \
  docs/superpowers/specs/2026-06-28-planner-ui-react-design.md \
  docs/superpowers/specs/2026-07-02-planner-ui-drawer-bulk-contrast-design.md \
  docs/superpowers/specs/2026-07-03-planner-ui-confidence-rationale-design.md \
  docs/superpowers/specs/2026-07-03-planner-ui-dark-theme-design.md \
  docs/superpowers/specs/2026-07-03-planner-ui-table-badge-conventions-design.md \
  docs/superpowers/specs/2026-07-04-planner-ui-confidence-hero-refinement-design.md \
  docs/superpowers/specs/2026-07-05-confidence-hero-turbofan-icon-design.md
```
Expected: 13 files removed. (If Step 1 surfaced additional filename-untagged frontend-only slice docs, add them here.)

- [ ] **Step 3: Verify the surviving-component docs are still present**

```bash
ls docs/superpowers/plans/2026-06-28-planner-ui-bff.md \
   docs/superpowers/plans/2026-07-01-planner-ui-part-context-dashboard.md \
   docs/superpowers/specs/2026-06-28-planner-ui-bff-design.md \
   docs/superpowers/specs/2026-07-01-planner-ui-part-context-dashboard-design.md
```
Expected: all four exist (they are reframed, not deleted, in Task 4).

- [ ] **Step 4: Commit**

```bash
git commit -m "docs: delete retired planner-ui frontend UI-slice specs/plans"
```

---

### Task 4: Reframe surviving-component docs + scrub incidental mentions

**Files:**
- Modify (reframe): `docs/superpowers/plans/2026-06-28-planner-ui-bff.md` + `docs/superpowers/specs/2026-06-28-planner-ui-bff-design.md`; `docs/superpowers/plans/2026-07-01-planner-ui-part-context-dashboard.md` + its `-design.md`
- Modify (scrub incidental): the apps/web + backend specs/plans that merely mention planner-ui — determined by grep

**Interfaces:**
- Consumes: the surviving-vs-incidental classification from Task 3's grep.

- [ ] **Step 1: List every remaining planner-ui mention in the docs tree**

```bash
grep -rniI "planner-ui\|planner_ui\|Planner UI\|Planner-UI" docs/superpowers | grep -v "2026-07-06-retire-planner-ui"
```

- [ ] **Step 2: Reframe the surviving-component docs**

In the BFF and part-context-dashboard spec/plan pairs, reword prose that frames the *frontend* as the consumer ("the Planner-UI", "Trax IO Review UI renders…") to the neutral "the web frontend (`apps/web`)". Keep the filename and all technical content — these document living BFF endpoints. Where the string "Planner-UI BFF" names the BFF component itself, it may stay (that is the surviving backend's name).

- [ ] **Step 3: Scrub incidental mentions elsewhere**

In `apps/web`'s own specs/plans and backend docs, remove the planner-ui *name* where a plain rewording preserves meaning (describe the feature intrinsically; e.g. "feature parity with planner-ui's CSV export" → "the CSV export feature"). Where a mention is load-bearing historical context, condense but do not distort. Do not delete any file in this task.

- [ ] **Step 4: Verify only intended references remain**

```bash
grep -rniI "planner-ui\|planner_ui\|Planner UI\|Planner-UI" docs/superpowers | grep -v "2026-07-06-retire-planner-ui" | grep -v "planner-ui-bff\|part-context-dashboard"
```
Expected: no hits, OR only deliberate residual "Planner-UI BFF" component-name references (each justifiable as the surviving backend's name). Report the final list.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers
git commit -m "docs: reframe surviving BFF docs; scrub incidental planner-ui mentions"
```

---

### Task 5: Code comment / docstring / test-name cleanup (behavior-neutral)

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/csv_export.py` (docstring lines ~3-6)
- Modify: `services/agent-spine/tests/bff/test_csv_export.py` (rename `test_csv_columns_are_the_14_planner_ui_columns_in_order`)
- Modify: `apps/web/src/features/part/writebackView.ts` (lines ~3, ~33), `apps/web/src/components/DemandTrend.tsx` (~8), `apps/web/src/lib/useTheme.ts` (~8), `apps/web/src/App.test.tsx` (~11)

**Interfaces:**
- Produces: no dangling comment/docstring referencing a deleted planner-ui path; identical runtime behavior.

- [ ] **Step 1: Rewrite the `csv_export.py` docstring**

Replace the module docstring's reference to `apps/planner-ui/src/lib/queryView.ts` as the column source-of-truth with a statement that the 14-column set + order is canonical **here** (this module). Keep the column semantics description. No code change below the docstring.

- [ ] **Step 2: Rename the CSV column test**

Rename `test_csv_columns_are_the_14_planner_ui_columns_in_order` → `test_csv_columns_are_the_14_canonical_columns_in_order` (drop `planner_ui`). Assertions unchanged.

- [ ] **Step 3: Reword the apps/web provenance comments**

In each of the four apps/web files, reword the "mirrors/verbatim-from planner-ui" comment to describe the behavior intrinsically (e.g. "The value dict keys are fixed by the writeback contract." / "Dependency-free inline-SVG demand trend." / "`:root` is dark by default; `.light` opts into light." / "Reset theme + storage in `afterEach` so tests don't leak."). No logic changes.

- [ ] **Step 4: Verify agent-spine suite green (behavior-neutral)**

```bash
cd services/agent-spine && uv run --extra bff --extra bvr pytest -q
```
Expected: same pass/skip counts as before the slice (the renamed test still passes); 0 failures. Also `uv run --extra dev ruff check .` clean.

- [ ] **Step 5: Verify apps/web suite green (behavior-neutral)**

```bash
cd apps/web && npm test && npm run build && npm run lint
```
Expected: 288 Vitest pass, build 0 errors, lint 0 errors (2 pre-existing shadcn warnings acceptable).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: drop dangling planner-ui path references from comments/docstrings/test name"
```

---

### Task 6: Rewrite `CLAUDE.md` to a single-frontend current state

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: ADR-0014 (Task 2).

- [ ] **Step 1: Enumerate the planner-ui touch points**

```bash
grep -n "planner-ui\|Planner-UI\|Planner UI\|8088" CLAUDE.md
```

- [ ] **Step 2: Edit the run/test command table**

Remove the `apps/planner-ui (React/TS — first frontend)` row entirely. In the `apps/web` row, change the parenthetical "the spec-faithful Trax Inventory Optimizer UI, alongside `apps/planner-ui`" to "the Trax Inventory Optimizer UI — the single frontend."

- [ ] **Step 3: Remove the planner-ui frontend paragraph and fix the Docker paragraph**

Delete the long "The **Planner-UI React frontend** (`apps/planner-ui/` …)" bullet/paragraph in full. In the "Local full-stack Docker deploy" paragraph, remove the `ui` service / `apps/planner-ui/Dockerfile` / `:8088` description and present `apps/web` on `:8089` as the frontend the BFF serves (keep the BFF + snapshot description). Keep the "Planner-UI BFF" name where it refers to the BFF component (surviving code). Where the `apps/web` paragraph says the four waves are "toward retiring `apps/planner-ui`", update to note the retirement is complete (cite ADR-0014).

- [ ] **Step 4: Verify no stale operational planner-ui references remain**

```bash
grep -n "apps/planner-ui\|:8088\|planner-ui/UAT\|planner-ui/Dockerfile" CLAUDE.md || echo "no stale frontend refs"
```
Expected: `no stale frontend refs` (any remaining "Planner-UI BFF" hits are the surviving backend name and are fine — verify each is a BFF reference).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md — apps/web is the single frontend; drop planner-ui"
```

---

### Task 7: Rewrite `ROADMAP.md` + `TASKS.md` to reflect the retirement

**Files:**
- Modify: `ROADMAP.md`, `TASKS.md`

**Interfaces:**
- Produces: trackers whose current-state sections describe one frontend + a dated retirement entry; non-planner-ui history preserved.

- [ ] **Step 1: Survey the hits**

```bash
grep -n "planner-ui\|Planner-UI\|Planner UI" ROADMAP.md
grep -c "planner-ui\|Planner-UI\|Planner UI" TASKS.md
```

- [ ] **Step 2: Update `ROADMAP.md`**

In current-status / frontend sections, state `apps/web` is the sole frontend and `apps/planner-ui` was retired (ADR-0014) after parity. Drop granular planner-ui phase/slice log lines (git retains them). Add a dated retirement entry consistent with the file's convention. Keep all non-planner-ui roadmap content.

- [ ] **Step 3: Update `TASKS.md`**

Rewrite the "current status / what's next" area to reflect the retirement (single frontend; planner-ui removed). Drop the granular planner-ui historical task lines rather than editing each in place (git retains them). Add a dated "Retired apps/planner-ui" entry. Preserve all non-planner-ui entries.

- [ ] **Step 4: Verify**

```bash
grep -ni "planner-ui" ROADMAP.md TASKS.md || echo "trackers clean of planner-ui"
```
Expected: `trackers clean of planner-ui` (or only a single intentional "retired planner-ui (ADR-0014)" entry per file — confirm that is all that remains).

- [ ] **Step 5: Commit**

```bash
git add ROADMAP.md TASKS.md
git commit -m "docs: ROADMAP/TASKS — record planner-ui retirement, single frontend"
```

---

### Task 8: Scrub the four guides + `apps/web/UAT.md` + recompile the `.docx`

**Files:**
- Modify: `docs/guides-src/01-architecture-guide.md`, `02-engineering-execution-guide.md`, `03-integration-handoff-guide.md`, `04-full-feature-guide.md`
- Modify: `apps/web/UAT.md` (the stale operational straggler — see Step 0)
- Regenerate: the corresponding `.docx` in `docs/guides/`

**Interfaces:**
- Produces: guide sources + `.docx` + `apps/web/UAT.md` presenting `apps/web` as the single frontend.

- [ ] **Step 0: Fix the `apps/web/UAT.md` operational straggler**

`apps/web/UAT.md` has 5 stale/broken references the retirement created: it links to the now-deleted `apps/planner-ui/UAT.md` (dead link), says `apps/planner-ui stays on :8088` (freed), and frames apps/web as "another consumer of the same BFF `apps/planner-ui` already exercises." Reword these so apps/web is the sole frontend/UAT surface: drop the dead `../planner-ui/UAT.md` links, drop the `:8088`/"stays on" line, and rephrase the "another consumer" / "planner-ui already exercises" phrasings to stand on their own (the BFF is exercised by apps/web). `grep -niE "planner" apps/web/UAT.md` must return no `apps/planner-ui` refs afterward (the "Planner-UI BFF" backend name, if present, may stay). Then continue with the guides below.

- [ ] **Step 1: Locate the pandoc build step and the .docx targets**

```bash
grep -rn "pandoc" docs/ *.md Makefile 2>/dev/null | head
ls docs/guides/
grep -n "planner-ui\|Planner UI\|Planner-UI\|8088" docs/guides-src/0{1,2,3,4}-*.md
```
Identify the exact pandoc invocation used to compile `guides-src/*.md` → `guides/*.docx` (reuse it verbatim; do not invent flags).

- [ ] **Step 2: Scrub each guide source**

Edit the four guide sources to present `apps/web` (`:8089`) as the single frontend and remove "two frontends" / planner-ui / `:8088` framing. Keep "Planner-UI BFF" where it names the surviving BFF. Where "what runs today" enumerates the frontend (esp. `04-full-feature-guide.md`), describe `apps/web` only.

- [ ] **Step 3: Recompile the affected `.docx`**

Run the pandoc command from Step 1 for each edited guide so `docs/guides/*.docx` matches its source. If pandoc is unavailable in the environment, STOP and report — do not leave `.docx` stale or hand-edit binary `.docx`.

- [ ] **Step 4: Verify sources clean + docx regenerated**

```bash
grep -ni "apps/planner-ui\|:8088" docs/guides-src/0{1,2,3,4}-*.md || echo "guide sources clean"
git status --porcelain docs/guides/
```
Expected: `guide sources clean` (residual "Planner-UI BFF" allowed if it names the BFF); the `.docx` files show as modified.

- [ ] **Step 5: Commit**

```bash
git add docs/guides-src docs/guides
git commit -m "docs(guides): single-frontend framing; recompile .docx"
```

---

### Task 9: Reframe sub-project #7's frontend in the foundational prose docs (→ apps/web)

**Files (grep-driven — the implementer re-greps to confirm the exact set):**
- `docs/design/2026-04-14-trax-io-inventory-optimizer-design.md` (~7 "Planner UI" hits)
- `docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md` (~6)
- `docs/exec/2026-04-14-trax-io-steering-committee-onepager.md` (~3)
- `docs/plans/2026-04-14-planner-ui-plan.md` + the other `docs/plans/2026-04-14-*.md` that mention "Planner UI" as sub-project #7 (bvr-pipeline, forecasting-policy, observability-soc2, tenant-onboarding-runbook, writeback-rest)
- `docs/superpowers/plans/2026-04-17-trax-io-recommendation-engine.md` + its `-design.md`; `services/recommendation-engine/README.md`
- `docs/contracts/2026-04-14-emro-event-publisher-contract.md` (verify its hit first — may be `:8088` or an eMRO ref)

**Interfaces:**
- Consumes: the 3-way naming rule + eMRO exception from the Scope amendment above.
- Produces: authoritative prose where sub-project #7's *frontend* reads as `apps/web`.

- [ ] **Step 1: Grep + classify every hit before editing**

```bash
grep -rniIE "planner.ui|Planner UI|Trax IO Review" docs/design docs/roadmap docs/exec docs/plans docs/contracts docs/superpowers/plans/2026-04-17* docs/superpowers/specs/2026-04-17* services/recommendation-engine/README.md
```
For each hit classify: (a) sub-project-#7 FRONTEND ref (was planner-ui) → reframe to `apps/web`; (b) **external eMRO Planner UI** (the eMRO product's own UI — e.g. "planner reviews in eMRO", "Writeback … Planner UI module", "Planner/Operator via eMRO") → LEAVE; (c) surviving "Planner-UI BFF" backend name → LEAVE; (d) the sub-project's own name "#7 Planner UI / Trax IO Review" as a roadmap/design *sub-project title* → reframe the frontend-implementation mention to apps/web but you may keep the sub-project's historical identifier where it's a structural label (e.g. "sub-project #7"). Report any ambiguous hit rather than guessing.

- [ ] **Step 2: Reframe the frontend references, surgically**

For category (a): change "Planner UI"/"Trax IO Review" *as the frontend implementation* to `apps/web` (e.g. "the Planner UI renders the approval queue" → "apps/web renders the approval queue"). PRESERVE all architecture, decisions, exit criteria, and non-frontend content — this is a targeted rename of the frontend implementation, NOT a rewrite of the locked design. Do not alter numbers, dates, or decisions. Note ADR-0014 where a "planner-ui retired" note fits naturally, but do not bloat these docs.

- [ ] **Step 3: Verify**

```bash
grep -rniIE "planner.ui|Planner UI|Trax IO Review" docs/design docs/roadmap docs/exec docs/plans docs/contracts docs/superpowers/plans/2026-04-17* docs/superpowers/specs/2026-04-17* services/recommendation-engine/README.md
```
Every remaining hit must be a justified category-(b) external-eMRO, category-(c) BFF-name, or a structural "sub-project #7" label — report the final list with a one-word justification each. No test suite (docs only, no code).

- [ ] **Step 4: Commit**

```bash
git add docs/design docs/roadmap docs/exec docs/plans docs/contracts docs/superpowers services/recommendation-engine/README.md
git commit -m "docs: reframe sub-project #7 frontend as apps/web across foundational docs"
```

---

### Task 10: Reframe sub-project #7's frontend node in the architecture diagrams

**Files:**
- Sources: `docs/diagrams/src/*.dot` (graphviz) + `docs/diagrams/src/*.mmd` (mermaid)
- Outputs: the rendered files under `docs/diagrams/png/` (note: several are `.svg`)

**Interfaces:**
- Consumes: the same 3-way rule; the eMRO exception is ESPECIALLY important here (diagrams have both an external "Planner / Operator (via eMRO Planner UI)" node AND a sub-plan-#7 "Planner UI (Trax IO Review)" node).

- [ ] **Step 1: Identify the toolchain**

```bash
which dot; dot -V 2>&1 | head -1        # graphviz for .dot
which mmdc || npx --yes @mermaid-js/mermaid-cli -V 2>&1 | head -1   # mermaid for .mmd
ls docs/diagrams/png/
```
Note what renders `.dot`→output and `.mmd`→output (check for any Makefile/script: `grep -rn "dot \|mmdc\|mermaid" docs/ Makefile 2>/dev/null`). If a renderer is unavailable, you will edit sources and FLAG the outputs as needing regeneration (do not leave a rendered diagram contradicting its source; `.svg` outputs are text and may be updated in-place to match if regeneration is impossible — but prefer real regeneration).

- [ ] **Step 2: Reframe ONLY the sub-plan-#7 frontend node**

In each `.dot`/`.mmd` source, find nodes labeled as the sub-project-#7 frontend — e.g. `Planner UI\n[sub-plan #7]`, `Planner UI<br/>Trax IO Review<br/>(sub-plan #7)`, `#7 Planner UI` — and reframe the label to `apps/web` (e.g. "apps/web\n(#7 frontend)" / "apps/web — Trax IO Review (#7)"). **DO NOT touch** the external nodes: `Planner / Operator`, `(via eMRO Planner UI)`, `eMROUI`, `Planner sign-off` — those are the human planner + the eMRO product UI, not our frontend. If a node's identity is ambiguous, leave it and flag it.

- [ ] **Step 3: Regenerate the outputs**

Render each edited source to its output with the toolchain from Step 1 (reuse any existing repo script/Makefile target verbatim). Confirm the output no longer shows the old "Planner UI [sub-plan #7]" frontend label. If no renderer is available, update the `.svg` text in-place to match the source label change and clearly report that binary/pipeline regeneration was not possible in this environment.

- [ ] **Step 4: Verify**

```bash
grep -rniIE "planner.ui|Planner UI|Trax IO Review" docs/diagrams/
```
Remaining hits must be only the external eMRO/operator nodes (report them). Sources and their rendered outputs must agree.

- [ ] **Step 5: Commit**

```bash
git add docs/diagrams
git commit -m "docs(diagrams): reframe sub-project #7 frontend node as apps/web; regenerate"
```

---

## Final verification (after Task 10, before finishing)

- **Working-tree sweep — every remaining planner reference is deliberately justified:**
```bash
grep -rniIE "planner.ui|planner_ui|Planner UI|Trax IO Review|:8088" . \
  --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=.superpowers \
  | grep -v "2026-07-06-retire-planner-ui" \
  | grep -v "docs/adr/2026-06-28-001[12]" \
  | grep -v "docs/adr/2026-07-06-0014"
```
Every surviving line must fall in a justified bucket: (1) the surviving **"Planner-UI BFF" / `PLANNER_*`** backend name (services/ + its docs); (2) the **external eMRO Planner UI** (target-stack + diagram operator nodes); (3) **deliberate retirement notes** naming `apps/planner-ui` + citing ADR-0014 (CLAUDE.md, ROADMAP.md, TASKS.md); (4) **Task-4-adjudicated historical citations** (real `file:LINE`/commit records in customer-testing-gap-remediation + bvr-pipeline docs). Produce the categorized list; zero *un*justified hits.
- **Docker:** `docker compose config --services` = `bff web` only.
- **Suites unchanged:** agent-spine (`--extra bff --extra bvr`) + apps/web (288 Vitest, build, lint) green (proves the doc/diagram slice changed no behavior).
- **Diagrams:** each rendered output agrees with its reframed source.
- Update `CLAUDE.md`/`ROADMAP.md`/`TASKS.md` + `.superpowers/sdd/progress.md` per the end-of-slice convention; the final whole-branch review confirms.

---

## Outcome (2026-07-06) — Tasks 9 & 10 DROPPED

After Task 8, a grounded read of the authoritative docs (design §6.2:194; guides 01-03; roadmap) showed sub-project #7 "Planner UI 'Trax IO Review'" is defined there as an **eMRO-embedded target** (eMRO-team-owned, hosted in eMRO, calling a *fictional* `trax-io-planner-bff`, with never-built surfaces) — **not** the retired `apps/planner-ui` app and **not** the standalone `apps/web` reference implementation. Those foundational docs contain **zero `apps/planner-ui` operational references** (verified). Blanket-reframing "#7 → apps/web" there would misrepresent the locked target architecture, so the user chose "leave the target docs." **Tasks 9 (foundational prose reframe) and 10 (diagram regen) are dropped.** The retirement is complete via Tasks 1-8: the app + Docker service + launch configs deleted, ADR-0014 recorded, UI-slice docs removed, surviving docs reframed, code comments cleaned, CLAUDE.md + ROADMAP + TASKS + guides + apps/web/UAT.md updated to single-frontend.
