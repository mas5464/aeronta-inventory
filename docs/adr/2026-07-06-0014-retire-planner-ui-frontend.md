# ADR-0014: Retire the `apps/planner-ui` frontend

**Date:** 2026-07-06
**Status:** Accepted
**Context project:** #7 Planner UI "Trax IO Review" (retirement; supersedes [ADR-0012](2026-06-28-0012-planner-ui-react-frontend.md))

## Context

[ADR-0012](2026-06-28-0012-planner-ui-react-frontend.md) built `apps/planner-ui` as the repo's first frontend, established to prove the BFF (ADR-0011) end-to-end with a lean, node-20.17-safe React stack. `apps/web` was then built independently as a second frontend over the same BFF — a React + Tailwind + shadcn/ui + TanStack Query app implementing the full PRD spec across 7 views (Overview, Part Drill-Down, Workbench, AI Recommendations, Forecast & Service Levels, What-If Scenarios, Data & Connections), plus a hardening slice (WCAG 2.1 AA, shared query-state handling, server-side pagination) and a documented four-wave feature-parity arc bringing it to CSV export, writeback history + rollback, the Reports/BVR view, and dark/light theme — closing every gap that previously distinguished `apps/planner-ui`.

With `apps/web` at full parity, the repo carried two independently-built frontends rendering the same BFF: duplicated component logic, two Vitest suites (236 vs 288 tests) to keep green, two Docker services (`:8088` vs `:8089`), two `UAT.md` plans, and every future BFF-contract change needing to land in both. This is redundant maintenance with no offsetting benefit — `apps/web` is not a subset of `apps/planner-ui`'s functionality, it is a superset built on a richer stack (Tailwind/shadcn/TanStack Query vs. hand-rolled CSS Modules) already carrying the PRD's full view set that `apps/planner-ui` never scoped to (digest, settings, auth, SSE remain deferred in both, per ADR-0012's and the `apps/web` hardening slice's own "Negative / deferred" sections).

The spec and implementation plan are at:
- [docs/superpowers/specs/2026-07-06-retire-planner-ui-design.md](../superpowers/specs/2026-07-06-retire-planner-ui-design.md)
- [docs/superpowers/plans/2026-07-06-retire-planner-ui.md](../superpowers/plans/2026-07-06-retire-planner-ui.md) (implementation plan, 8 tasks)

## Decision

Retire the `apps/planner-ui` frontend. Keep the shared BFF backend (`services/agent-spine/src/trax_io_spine/bff/` — `PlannerStore`, `create_planner_app`, the `PLANNER_SNAPSHOT_DIR`/`PLANNER_RECS_FILE` env precedence) **unchanged**, and make `apps/web` the single frontend going forward.

- **Frontend-only retirement.** No BFF route, model, or env var changes. The BFF's HTTP contract (OpenAPI) is exactly what it was before this ADR — `apps/web` already consumes it, and nothing downstream of the BFF (extract, feature store, recommendation engine, guardrail, writeback) is touched.
- **`apps/planner-ui` is deleted** (app source, its Docker `ui` service, and its dev launch config) — a prior commit on this branch. Its design record is not lost: git history retains the full tree, and ADR-0012 remains in the repo, now marked superseded, as the durable rationale for why it was built the way it was.
- **The BFF's `Planner*` naming is intentionally retained** as surviving-code identity (`PlannerStore`, `create_planner_app`, `PlannerClient`-shaped wire models) even though the `apps/planner-ui` frontend that motivated the name is gone — renaming live, tested backend code purely to chase a retired frontend's name would be churn for its own sake, not a functional or clarity improvement. The BFF is not "the planner-ui backend"; it is the one BFF that both frontends were always meant to share, and `apps/web` is now its sole consumer.
- **ADR-0012 is superseded, not deleted**, per the repo's documented exception to "erase all trace" for retirements: ADRs are the historical decision record and stay intact, with a `Superseded by` status note added.

## Consequences

**Positive**
- One frontend to build, test, and ship (`apps/web`, 288 Vitest tests, build + lint clean) instead of two.
- Docker host `:8088` is freed; `docker-compose.yml` now runs a single `web` service (`:8089`) alongside the BFF.
- The BFF and its HTTP contract are completely unaffected — zero risk to `services/agent-spine`, zero re-test burden on the backend.
- `apps/web`'s existing UAT plan (`apps/web/UAT.md`) becomes the sole UAT surface for the Planner UI; `apps/planner-ui/UAT.md` is retired along with its app.
- Full historical context is preserved: `apps/planner-ui`'s design decisions remain readable in ADR-0012 (now superseded) and in git history, so future readers understand why it existed and why it was safe to retire.

**Negative / deferred**
- Any bookmark, script, or external reference to `http://localhost:8088` (the retired `apps/planner-ui` Docker host) breaks; `apps/web` on `:8089` is the replacement.
- The two frontends' independent implementations (CSS Modules vs. Tailwind/shadcn) mean no code was literally reused in closing `apps/web`'s parity gaps — each wave (CSV export, history/rollback, BVR, theme) was a fresh build against the BFF contract, not a port. This was accepted as the cost of the two-frontend experiment, not a cost of retiring it.
- CLAUDE.md's `apps/planner-ui` narrative (run/test commands, the full phase-by-phase build history) is left in place as historical record per the plan's erase-all-trace exception for ADRs — but CLAUDE.md itself is a living doc, not an ADR, and a follow-up task in this same plan repoints it at this ADR rather than the retired app's row.

## Alternatives considered

1. **Keep both frontends indefinitely.** Rejected: no user-facing benefit from running two UIs against one BFF, and every future BFF-contract change (a new field, a new endpoint) would need parallel updates and parallel test-suite maintenance in both, permanently.
2. **Retire `apps/web` and keep `apps/planner-ui` instead.** Rejected: `apps/web` implements the full PRD view set (7 views vs. planner-ui's ops-console-style subset) and already reached feature parity on every planner-ui-only capability across four merged waves; discarding the more complete, more recently validated implementation would be the wrong direction.
3. **Merge the two into a single new frontend.** Rejected as unnecessary extra work: `apps/web` already *is* the merged superset in practice (built independently to the full spec, then backfilled with every planner-ui capability); a literal merge would just reproduce `apps/web` at higher cost and risk.
4. **Delete ADR-0012 along with the app, for a truly clean slate.** Rejected: the plan's global constraint carves out ADRs as the one erase-all-trace exception — decision records document *why*, and future readers (including future agents) benefit from seeing the full arc from "why we built planner-ui" (ADR-0012) to "why we retired it" (this ADR) rather than a silent gap.
