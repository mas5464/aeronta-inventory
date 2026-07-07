# apps/web: Writeback History + Rollback (Feature-Parity Wave 2 of 4)

## Context

Wave 2 of the four-part effort bringing `apps/web` ("Trax Inventory
Optimizer") to full feature parity with the retired `apps/planner-ui` ("Trax
IO Review"), ahead of retiring it. Wave 1 (CSV export) shipped
(`docs/superpowers/plans/2026-07-06-apps-web-csv-export.md`). The remaining
gaps after this wave: Reports/BVR view (Wave 3), dark/light theme (Wave 4).

`apps/web` today has **no writeback-history or rollback UI at all** (confirmed
by a source-grounded gap analysis). `apps/planner-ui` had a full version
timeline plus a "roll back last change" button in its per-recommendation
`DetailPanel`. This wave brings the same capability to `apps/web` over the
**same BFF endpoints** — no backend changes needed; the routes already exist.

Standing constraint for the whole parity effort (confirmed with the user):
`apps/web` must remain embeddable in eMRO later (iframe/module). Nothing here
requires new work toward that (HashRouter already in use; iframes isolate CSS).

## What already exists (verified against source)

**BFF endpoints** (`services/agent-spine/src/trax_io_spine/bff/app.py:178-184`):
- `GET /v1/tenants/{tenant_id}/history?pn={pn}&location={location}` →
  `list[HistoryEntry]` (`pn`/`location` are query params, not path params).
- `POST /v1/tenants/{tenant_id}/rollback` with a `RollbackRequest` body →
  `RollbackResult`.

**Contract shapes** (`trax_io_spine.contracts`, verified by introspection):
- `HistoryEntry`: `tenant_id, pn, location, version: int,
  status: WritebackStatus, old_values: dict[str,int] | None,
  new_values: dict[str,int], provenance_id: str, tier: AutonomyTier | None,
  agent_version: str, changed_by_principal: str, idempotency_key: str | None,
  parent_version: int | None, changed_at: datetime`.
- `RollbackRequest`: `tenant_id, pn, location, reason: str, principal: str,
  requested_at: datetime`.
- `RollbackResult`: `tenant_id, pn, location, status: RollbackStatus,
  from_values: dict[str,int] | None, to_values: dict[str,int] | None,
  reverted_from_version: int | None, new_version: int | None,
  rolled_back_at: datetime | None, error_message: str | None`.
- `WritebackStatus` values: `written, deferred_open_order, failed, shadowed`.
- `RollbackStatus` values: `rolled_back, outside_window, nothing_to_revert`.

**Prior review UI's reference behavior** (`apps/planner-ui/src/components/DetailPanel.tsx`,
`hooks/usePlanner.ts`): newest-first timeline `<ol>`; each row = `v{version}`,
status badge, `new_values` summary, `{changed_at} · {changed_by_principal}`.
"Roll back last change" is disabled unless `revertible` — computed as
`latestWrite = [...history].reverse().find(e => e.status === "written")` and
`latestWrite != null && latestWrite.old_values !== null`. It fired
rollback one-click with hardcoded `reason: "planner rollback"`,
`principal: "planner"`, and **no confirmation** — a gap the session's UX audit
flagged. `apps/web` improves on this (see Design).

**apps/web patterns to reuse** (verified):
- Enum types are string unions: `export type TaskStatus = "pending" | ...`
  (`types.ts:130`). `PolicyView` is `{ rop, eoq, safety_stock, max_stock }`
  (`types.ts:45`).
- Mutations: `useMutation<Result, Error, Vars>` + `useQueryClient` +
  `queryClient.invalidateQueries({ queryKey: [...] })` on success
  (`useRecommendations.ts`).
- Confirm dialog: `RejectDialog` (`features/workbench/RejectDialog.tsx`) —
  `useRef` + `useFocusTrap(containerRef, onCancel)`, `role="dialog"
  aria-modal="true"`, `onCancel`/`onConfirm(...)`/`isSubmitting` props. This
  is the exact pattern the rollback confirm dialog mirrors.
- Shared `<QueryState>` (`components/QueryState.tsx`) for
  loading/error(+Retry)/empty.
- Workbench rows already link the part number to
  `/parts/{pn}/{location}` (`Workbench.tsx:353`); Part Drill-Down reads
  `useParams<{ pn; location }>` (`features/part/PartDrillDown.tsx`).

## Design

### Data layer — `apps/web/src/lib/api/`

**`types.ts`** gains:
- `export type WritebackStatus = "written" | "deferred_open_order" | "failed" | "shadowed";`
- `export type RollbackStatus = "rolled_back" | "outside_window" | "nothing_to_revert";`
- `export interface HistoryEntry { ... }` — all 14 fields above.
  `old_values`/`new_values` are `Record<string, number>` (with
  `old_values: Record<string, number> | null`). `tier: AutonomyTier | null`.
  `changed_at: string` (ISO). `idempotency_key`/`parent_version` nullable.
- `export interface RollbackRequest { tenant_id, pn, location, reason: string,
  principal: string, requested_at: string; }`
- `export interface RollbackResult { ... }` — all 10 fields above; value dicts
  and version/timestamp fields nullable, `error_message: string | null`.

**`client.ts`** gains two methods (mirroring the existing `request<T>` +
`URLSearchParams` idioms):
- `getHistory(pn: string, location: string, tenant = DEFAULT_TENANT):
  Promise<HistoryEntry[]>` → `GET .../history?pn=&location=` (both URL-encoded).
- `rollback(req: RollbackRequest, tenant = DEFAULT_TENANT):
  Promise<RollbackResult>` → `POST .../rollback` with the JSON body.

**`useHistory.ts`** (new) / additions to `useRecommendations.ts`:
- `historyQueryKey(tenant, pn, location)` = `["history", tenant, pn, location]`.
- `useHistory(pn, location, tenant?)` — `useQuery` on that key, `enabled` only
  when both `pn` and `location` are non-empty (mirrors `usePartContext`'s
  `enabled` guard so a missing param can't fire a bad request — and note the
  session's earlier finding that `retry: false` is already the app-wide
  QueryClient default, so a 404/empty resolves cleanly to an error/empty state
  rather than hanging).
- `useRollback(tenant?)` — `useMutation<RollbackResult, Error, RollbackRequest>`
  that on success calls `queryClient.invalidateQueries({ queryKey: ["history",
  tenant] })` so the timeline refetches and the new rollback entry appears.
  **Only** the history query is invalidated — rollback writes to the writeback
  ledger, and the part-context `current_policy` is sourced from the
  feature-store snapshot (unaffected), so no broader refetch.

### UI piece 1 — Writeback history section on Part Drill-Down

A new `WritebackHistory` component (`features/part/WritebackHistory.tsx`),
rendered as a card below the existing Part Drill-Down sections, given `pn` +
`location` (from the same `useParams` the page already reads). It:
- Calls `useHistory(pn, location)`; renders loading/error(+Retry)/empty through
  `<QueryState>` with empty copy `"No prior writes for {pn} · {location}."`.
- Renders a newest-first `<ol>` timeline: each row shows
  `v{version}`, a status badge (color-coded by `WritebackStatus`, text label —
  color-not-only), the `new_values` summary formatted as
  `ROP {rop} · EOQ {eoq} · SS {safety_stock} · Max {max_stock}` via a small
  shared formatter, and `{changed_at, formatted} · {changed_by_principal}`.
- The section's container has `id="history"` so the Workbench deep-link (piece
  3) can scroll to it.

**Provenance-invariant note:** history rows are audit *events*, not derived
metric values, so they render as a plain timeline WITHOUT `ProvChip`. The
provenance invariant governs `MetricValue<T>` rendering; a `HistoryEntry`
already carries its own `provenance_id` + `changed_by_principal` lineage inline.
This is a deliberate, documented boundary — not an omission.

### UI piece 2 — Rollback with confirm dialog

- A "Roll back last change" button in the history card header, `disabled`
  unless `revertible` (the same rule the prior review UI used: the latest
  `written` entry exists and has a non-null `old_values`). When not revertible,
  a `title`
  explains why ("Nothing to roll back — no prior agent-applied value is on
  record").
- Clicking opens a `RollbackConfirmDialog`
  (`features/part/RollbackConfirmDialog.tsx`) modeled on `RejectDialog`:
  `useFocusTrap(containerRef, onCancel)`, `role="dialog" aria-modal="true"`,
  focus-trap + Escape-close + focus restoration to the button. It shows what
  will revert — the latest write's `new_values` (from) → `old_values` (to),
  formatted with the shared policy formatter — and a required **reason**
  text input. Confirm is disabled until the reason is non-empty.
- On confirm, fires `useRollback` with `{ tenant_id, pn, location, reason,
  principal: "planner", requested_at: new Date().toISOString() }`
  (`principal` hardcoded — apps/web has no auth yet).
- Result feedback: on `rolled_back`, the dialog closes and the timeline
  refetches (showing the new entry). On a non-success `RollbackStatus`
  (`outside_window` / `nothing_to_revert`) or an `error_message`, surface it
  inline in the dialog (or as an alert) rather than silently closing.

### UI piece 3 — Workbench "History" deep-link

Each Workbench row gains a small "History" link (alongside the existing
part-number link) to `/parts/{pn}/{location}#history`. Part Drill-Down honors
the `#history` hash on mount: a small effect scrolls the `#history` section
into view (`scrollIntoView`) once the page has rendered. This gives a planner
who just approved a write a labeled, one-click path to that part's audit trail.

## Testing

**Client** (`client.test.ts`): `getHistory` builds
`.../history?pn=…&location=…` (URL-encoded) and returns the parsed array;
`rollback` POSTs the `RollbackRequest` body to `.../rollback`; both surface a
non-OK response as an `ApiError` (mirrors existing client tests).

**Hooks**: `useHistory` is disabled when `pn`/`location` empty; `useRollback`
invalidates the `["history", tenant]` key on success (assert via a spy/mock
`queryClient`, matching how existing mutation hooks are tested if such tests
exist — otherwise cover the invalidation via the component test).

**`WritebackHistory` component**: renders a timeline from a mocked history
(newest-first order, value summary, status badge text); empty state copy when
`[]`; error+Retry when the query errors; the rollback button is **disabled**
when the latest `written` entry has `old_values === null` (and when history is
empty), **enabled** when a revertible entry exists.

**`RollbackConfirmDialog`**: opens on button click; Confirm disabled until a
reason is entered; entering a reason + Confirm calls the rollback mutation with
the typed reason and `principal: "planner"`; Escape/Cancel closes without
firing; focus-trap wraps (reuse `useFocusTrap`'s tested behavior — assert the
dialog uses it, don't re-test the hook). A non-success `RollbackResult`
(`nothing_to_revert`) surfaces its message rather than closing silently.

**Workbench**: each row renders a "History" link whose `href` is
`#/parts/{encoded pn}/{encoded location}#history`.

**Live Docker verification** (rebuild bff+web; stack already dockerized):
approve a recommendation in the Workbench → open that part's drill-down →
confirm the write shows in the Writeback history timeline → roll it back via
the confirm dialog → confirm a `rolled_back` entry appears and the button
state updates. Also verify the Workbench "History" link lands on the
`#history` section.

## Out of scope

- Any change to the (now-retired) `apps/planner-ui` — it kept its existing history/rollback UI unchanged for the duration of this parity effort.
- Any BFF change — the `history`/`rollback` routes already exist and are
  unchanged.
- Waves 3–4 (Reports/BVR view, dark/light theme).
- Bulk rollback, rollback-to-arbitrary-version, or a global audit-log view —
  the BFF exposes only single-key latest-write rollback; matching that.
- Auth/real principal — `principal: "planner"` is hardcoded.
