# Trax IO Review — Planner UI

The React frontend for #7 "Trax IO Review" — the planner's approval queue. It renders
the [Planner BFF](../../services/agent-spine/src/trax_io_spine/bff/) (ADR-0011): a
priority-sorted recommendation queue, a provenance detail panel, approve / reject / defer,
and a per-tenant kill switch.

## Stack

React 18 · TypeScript 5 · Vite 5 · Vitest 2 + React Testing Library · CSS Modules.
Pinned to a node-20.17-safe stack (no router, no data lib, no component lib).

## Develop & test

```bash
npm install          # node_modules is gitignored — never committed (iCloud-safe)
npm test             # Vitest (23 tests)
npm run build        # tsc typecheck + vite production build
npm run dev          # dev server
```

Two data modes for `dev`:

- `VITE_FAKE=1 npm run dev` — fully offline against the in-memory `FakePlannerClient` + `SAMPLE_SEED`.
- `VITE_BFF_URL=http://localhost:8000 npm run dev` — against a running BFF
  (`uvicorn` over `trax_io_spine.bff.app:create_planner_app`).

## Layout

- `src/api/` — `types.ts` (TS mirrors of the BFF wire models), `client.ts`
  (`PlannerClient` interface · `HttpPlannerClient` · `FakePlannerClient`), `sample.ts`.
- `src/components/` — `KillSwitchHeader`, `QueueTable`, `DetailPanel` (each with a `.module.css` + test).
- `src/hooks/usePlanner.ts` — queue + selection + actions over a `PlannerClient`.
- `src/App.tsx` — composition; the kill switch disables only **approve** (writes), never reject/defer.

## Scope (this slice)

Core approval loop only. Deferred to follow-ups: bulk-approve filter builder, the
history/rollback timeline, the Tier-C digest, settings, routing, SSE, auth, and the
real eMRO embedding.

> The repo lives in iCloud Drive; `node_modules` is gitignored and built outside it.
> If `npm install` here produces `· 2` conflict copies, exclude the repo from iCloud.
