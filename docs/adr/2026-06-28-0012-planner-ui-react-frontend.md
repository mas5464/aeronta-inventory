# ADR-0012: #7 Planner UI — React frontend (the repo's first frontend)

**Date:** 2026-06-28
**Status:** Accepted
**Context project:** #7 Planner UI "Trax IO Review" (React frontend; consumes the BFF from [ADR-0011](2026-06-28-0011-planner-ui-bff.md))

## Context

The BFF (ADR-0011) exposes the approval queue, provenance, actions, history, and the kill switch over HTTP. This slice builds the **first frontend in the repo** — a React app for the **core approval loop** (queue → provenance detail → approve/reject/defer → kill switch). The repo was all-Python (uv + pytest); this introduces a Node toolchain.

Two environment constraints shaped the approach:
1. **The repo lives in iCloud Drive.** A `node_modules` tree (tens of thousands of files) would be mangled into `· 2` conflict copies — far worse than the test-file churn already seen four times this session.
2. **Node is 20.17**, below the very latest Vite's `≥20.19` requirement.

## Decision

Build the frontend at `apps/planner-ui/` with a **pinned, node-20.17-safe stack**: React 18 · TypeScript 5 · **Vite 5** · **Vitest 2** + React Testing Library · **CSS Modules** (Vite-native, zero extra deps; no router, no data lib, no component lib).

- **Build/test in the scratchpad (outside iCloud); commit source-only.** All `npm install` / `vite` / `vitest` work happens in a scratchpad working copy; only source + `package-lock.json` are copied into the repo (`node_modules`/`dist` gitignored — as with any JS project). The committed artifact is verified source; `cd apps/planner-ui && npm install && npm test` reproduces it.
- **Talks to the BFF via a typed `PlannerClient`** mirroring the BFF wire models: `HttpPlannerClient` (fetch, maps non-2xx → `PlannerError` carrying the BFF `detail`, distinguishing 423/409/404) for real use, and `FakePlannerClient` (in-memory, lifecycle-faithful) for tests and offline `VITE_FAKE=1` dev.
- **Components:** `QueueTable` (priority-desc, tier badges, criticality, keyboard-operable row selection, per-row approve gated by `approvable`), `DetailPanel` (current→proposed diff, why-queued, evidence, approve/reject-with-reason/defer), `KillSwitchHeader` (toggle; engaged ⇒ approve disabled), composed via a `usePlanner` hook.
- **The kill switch disables only approve** (the write); reject/defer never write, so they stay enabled when the agent is paused.

### Review-driven changes (3-lens adversarial review)

A 3-lens opus review (React correctness · a11y/UX · BFF-contract fidelity) caught real issues, fixed before merge:
- **a11y:** row selection is now a keyboard-operable button (was a non-focusable `<tr onClick>`); criticality is exposed as screen-reader text (was color-only); a global `:focus-visible` ring; dark-mode criticality tokens.
- **Contract fidelity:** `aog_risk_level` is an `IntEnum` on the wire → typed `number` (0–4), not `string`. (`estimated_cost_impact` is a Decimal serialized as a string — handled by `number | string` + `Number()` coercion, verified against the live BFF.)
- **UX/consistency:** advisory rows (no writable policy) now disable approve via a new **`QueueRow.approvable`** flag on the BFF (was an enabled button guaranteed to 409).

## Consequences

**Positive**
- A working, demonstrable Planner UI: queue → provenance → approve/reject/defer → kill switch, verified live (25 Vitest tests, `tsc` + `vite build` clean, screenshots of both states).
- iCloud never touches `node_modules`; the committed tree is small, clean source.
- The typed client makes the BFF↔UI contract explicit; `FakePlannerClient` enables fully offline dev/test.

**Negative / deferred**
- Scope is the core loop. Deferred: bulk-approve filter builder, history/rollback timeline, Tier-C digest, settings (autonomy-bands/service-level), routing/tabs, SSE real-time, auth/SSO, real eMRO embedding, full WCAG audit.
- Known minor follow-ups from the review: an `AbortController`/sequence-token guard on `getDetail` (stale-detail race under real latency) and a pending-state guard against double-submit.
- `apps/planner-ui` can't run its tests without `npm install` first (normal for JS; documented in the README + CLAUDE.md).

## Alternatives considered

1. **Build in the repo directly.** Rejected: `npm install` would put `node_modules` in iCloud → conflict-copy corruption.
2. **Tailwind / a component library.** Rejected for v1: CSS Modules are Vite-native with zero extra deps — the smallest dep tree, which matters most given the iCloud sensitivity.
3. **The repo-based subagent SDD workflow.** Adapted: the scratchpad has no git branch and the Node toolchain is env-sensitive, so the frontend was built inline with tight TDD loops, then verified by a multi-lens adversarial review workflow over the committed diff.
