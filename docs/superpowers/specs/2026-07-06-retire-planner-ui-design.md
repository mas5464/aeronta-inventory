# Retire `apps/planner-ui` — Design

## Context

`apps/web` ("Trax Inventory Optimizer") reached full feature parity with the
legacy `apps/planner-ui` ("Trax IO Review") across four merged waves (CSV
export, writeback history + rollback, Reports/BVR, dark/light theme — all on
`main` as of `b78fc3b`). Two frontends over one BFF is now redundant. This
slice retires `apps/planner-ui` so the repo has a single frontend.

The user chose **"erase all trace"** for planner-ui's historical planning
record (over "preserve history") and **"yes — new ADR + supersede note"** for
recording the decision.

## The load-bearing scope boundary (read first)

**This retires the planner-ui FRONTEND APP. It does NOT rename or alter the
BFF backend.**

The BFF (`services/agent-spine/src/trax_io_spine/bff/`) is surviving shared
infrastructure that `apps/web` depends on. It is internally named "Planner-UI
BFF" — `PlannerStore`, `create_planner_app()`, the `trax-io-spine` CLI, and
the `PLANNER_SNAPSHOT_DIR` / `PLANNER_TENANT` env vars all carry the "Planner"
name. **None of that is touched.** Renaming a living backend's public
identity (classes, modules, env vars, the deploy entrypoint) is a large,
risky change far outside "retire the redundant frontend," and would gain
nothing operationally.

Therefore **"erase all trace" scopes to:**
- the planner-ui frontend app and its build output,
- its **frontend-only** UI-slice planning docs,
- operational references that would mislead a current reader (the Docker
  service, the `launch.json` dev configs, `CLAUDE.md`'s run/test
  instructions, guides describing "two frontends"), and
- dangling code comments that cite now-deleted planner-ui file paths.

It explicitly does **NOT** scope to: the surviving BFF's `Planner*` naming,
or the BFF's own design docs (which document living code). A post-retirement
`grep -i planner` will still legitimately match BFF code and BFF docs — that
is the surviving component, correctly retained.

**Git preserves the full history regardless.** "Erase all trace" means the
current working tree's docs no longer surface the retired frontend; every
deleted/rewritten doc remains recoverable via `git log`.

## Delete

- **`apps/planner-ui/`** — the entire directory (147 MB incl. `node_modules`,
  `dist`; source, tests, `Dockerfile`, `nginx.conf`, `UAT.md`, `README.md`).
- **`docker-compose.yml` → the `ui:` service** — remove it entirely (frees
  host `:8088`). Keep `bff` and `web`. Update the file header comment (it
  currently says "Planner UI + BFF" and "open http://localhost:8088 the
  Planner UI") to describe the web UI on `:8089` as the app.
- **`.claude/launch.json`** — remove the `planner-ui-dev` and
  `planner-ui-fake` configurations. Keep `web-dev`.
- **Frontend-only planner-ui UI-slice specs/plans.** These document the
  retired app's own UI features and have no surviving-code counterpart.
  Determined by **content grep, not filename** (some are not filename-tagged):
  - Plans: `2026-07-02-planner-ui-drawer-bulk-contrast.md`,
    `2026-07-03-planner-ui-confidence-rationale.md`,
    `2026-07-03-planner-ui-dark-theme.md`,
    `2026-07-03-planner-ui-table-badge-conventions.md`,
    `2026-07-04-planner-ui-confidence-hero-refinement.md`,
    `2026-07-05-confidence-hero-turbofan-icon.md` (planner-ui ConfidenceHero
    work — filename lacks "planner").
  - Specs: the `-design.md` counterpart of each of the above, **plus**
    `2026-06-28-planner-ui-react-design.md` (the frontend app's design spec —
    its decision record is preserved in ADR-0012, kept + superseded below).
  - The plan's implementer MUST content-grep `docs/superpowers/{plans,specs}/`
    for planner-ui references and classify each hit as frontend-only-slice
    (delete) vs surviving-component (keep, below) vs incidental-mention
    (scrub, below) — the lists here are the known set, not a substitute for
    the grep.

## Keep + reframe (surviving-component docs)

These name a **surviving** shared component (the BFF and its endpoints that
`apps/web` calls), so deleting them would erase a living component's design
record. Keep the files (and their filenames — they name surviving code);
reword only prose that implies the retired *frontend* is the consumer, to
"the web frontend / BFF":
- `docs/superpowers/plans/2026-06-28-planner-ui-bff.md` + spec
  `2026-06-28-planner-ui-bff-design.md` — the BFF itself.
- `docs/superpowers/plans/2026-07-01-planner-ui-part-context-dashboard.md` +
  spec — built the part-context + dashboard BFF endpoints `apps/web` uses.

## Keep + scrub incidental mentions

`apps/web`'s own specs/plans and the backend specs/plans that merely *mention*
planner-ui (e.g. as the parity target, or "mirrors planner-ui"). Remove the
planner-ui **name** where rewording preserves meaning (describe the feature
intrinsically); where a mention is load-bearing historical context, condense
but do not mangle. Do not delete these files. Known set (implementer
re-greps): `2026-07-01-new-web-ui-build-plan.md`, the four
`2026-07-06-apps-web-*` plan/spec pairs, `2026-07-01-real-emro-wave1/wave3`,
`2026-07-02-bvr-pipeline-v1-local`, `2026-07-02-fast-boot-feature-store-snapshot`,
`2026-07-04-customer-testing-gap-remediation`,
`2026-07-05-fulfillment-decision-agent-wave-a-design`.

## Rewrite to current state (live docs)

- **`CLAUDE.md`** (8 hits): remove the `apps/planner-ui` row from the
  run/test table; remove the long "Planner-UI React frontend" paragraph;
  update the "Local full-stack Docker deploy" paragraph to drop the `ui`
  service / `:8088` and present `apps/web` on `:8089` as the frontend; adjust
  the `apps/web` row's "alongside `apps/planner-ui`" framing to "the frontend."
  Keep every BFF and `apps/web` sentence. Add a one-line pointer to ADR-0013.
  The BFF bullet's "Planner-UI BFF" name stays (surviving code).
- **`ROADMAP.md`** (20 hits) and **`TASKS.md`** (59 hits): rewrite the
  current-status / active sections to state `apps/web` is the sole frontend
  and planner-ui is retired; drop the granular planner-ui historical log lines
  (git retains them); add a dated retirement entry. Preserve all non-planner-ui
  history.
- **Guides** — all four `docs/guides-src/0{1,2,3,4}-*.md` mention planner-ui.
  Scrub each to present `apps/web` as the single frontend, then **recompile
  the corresponding `.docx`** via the repo's existing pandoc step (the guides
  are shipped as `.docx`; a stale `.docx` would contradict the source).
- **Dangling code comments/docstrings** that cite now-deleted planner-ui
  paths — reword so none references a deleted file:
  - `services/agent-spine/src/trax_io_spine/bff/csv_export.py:3-6` (cites
    `apps/planner-ui/src/lib/queryView.ts` as the column source-of-truth) →
    state the 14-column set is canonical here now; drop the dead path.
  - `services/agent-spine/tests/bff/test_csv_export.py:39` (test name
    `test_csv_columns_are_the_14_planner_ui_columns_in_order`) → rename to
    drop `planner_ui`.
  - `apps/web/src/features/part/writebackView.ts:3,33`,
    `apps/web/src/components/DemandTrend.tsx:8`,
    `apps/web/src/lib/useTheme.ts:8`,
    `apps/web/src/App.test.tsx:11` — reword the "mirrors/verbatim-from
    planner-ui" provenance comments to describe the behavior intrinsically
    (these are apps/web source; they must not point at a deleted app).
  - These are comment/docstring-only edits; **no code behavior changes**, so
    the existing apps/web and agent-spine test suites must stay green
    unchanged (the renamed test is the only test-identifier change).

## ADR handling (the erase-all-trace exception)

Per the user's Q2 choice, ADRs are preserved (a "Superseded by" note requires
the superseded ADR to exist), making them the one deliberate exception to
erase-all-trace:
- **New `docs/adr/2026-07-06-0013-retire-planner-ui-frontend.md`** — records:
  context (parity reached across 4 waves), decision (retire the planner-ui
  frontend; keep the shared BFF backend and `apps/web`), consequences
  (single frontend; `:8088` freed; BFF unchanged). Follow the existing ADR
  format/numbering in `docs/adr/`.
- **`docs/adr/2026-06-28-0012-planner-ui-react-frontend.md`** — add a
  "Status: Superseded by ADR-0013" header near the top; leave the body intact
  as the historical decision record.
- **`docs/adr/2026-06-28-0011-planner-ui-bff.md`** — unchanged (documents the
  surviving BFF).

## Verification

- `docker compose config` parses with no `ui` service and no error.
- `docker compose up --build bff web` (scoped to this project only — never
  touches `oracle19c`/MySQL): the web UI serves on `:8089` and its `/v1`
  calls proxy to the BFF (same-origin), i.e. retiring planner-ui did not
  disturb the surviving stack. `docker compose down` after.
- `apps/web` gate unchanged: `npm test` (288 Vitest) + `npm run build` +
  `npm run lint` green — proves the comment/rename edits changed no behavior.
- `services/agent-spine` gate unchanged: `uv run --extra bff --extra bvr
  pytest` green — proves the `csv_export.py` docstring + renamed test are
  behavior-neutral.
- `grep -rniI "planner-ui" .` over the working tree (excluding
  `node_modules`, `.git`, `.superpowers/`, and this retirement slice's own
  spec/plan/ADR-0013) returns hits ONLY in: the surviving BFF code/docs and
  their `Planner*` identity, ADR-0011/0012 (by design), and git history —
  never a dangling reference to the deleted frontend or its files.
- `.superpowers/sdd/progress.md` + trackers updated per the repo's
  end-of-slice convention.

## Out of scope

- Renaming the BFF backend or any `Planner*` symbol / `PLANNER_*` env var
  (surviving code; separate large decision if ever wanted).
- Any `apps/web` behavior change (comment/test-name edits only).
- Any change to the BFF's HTTP contract, endpoints, or the recommendation /
  feature-store / forecasting / event-publisher packages.
- Deleting ADR-0011/0012 (kept by the ADR exception above).
- Removing planner-ui from git history (impossible and unwanted; history is
  the truthful record).
