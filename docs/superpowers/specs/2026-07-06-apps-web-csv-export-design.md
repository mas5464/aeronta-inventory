# apps/web: CSV Export (Feature-Parity Wave 1 of 4)

## Context

This is the first of a four-part effort to bring `apps/web` ("Trax Inventory
Optimizer") to full feature parity with `apps/planner-ui` ("Trax IO Review"),
so that `apps/planner-ui` can eventually be retired and `apps/web` becomes the
one full-feature application. A source-grounded gap analysis (this session)
found four genuine gaps — CSV export, writeback history + rollback, a
Reports/BVR view, and a dark/light theme — plus one polish-level difference
(a richer `ConfidenceHero`-style recommendation card) that is not part of this
plan. Everything else initially suspected as unique to `planner-ui` (kill
switch, bulk-approve, dashboard KPI breakdowns) already exists in `apps/web`
in some form.

This spec covers gap #1: CSV export. The agreed order for the remaining three
is: writeback history + rollback → Reports/BVR view → dark/light theme, each
its own future brainstorm/spec/plan cycle. `apps/planner-ui` retiring is the
mechanical last step once all four ship — out of scope here.

A standing constraint for the whole four-part effort, confirmed with the
user: the unified `apps/web` must remain able to run embedded inside eMRO
(iframe/native module) later, per `apps/planner-ui`'s original design intent
(design doc, Sub-project #7). Nothing in this specific spec requires new work
toward that — both apps already use `HashRouter`, and iframes isolate CSS
naturally — but it is a standing design constraint for future specs in this
arc, not something to forget.

## What `apps/planner-ui` does today (the reference behavior)

`apps/planner-ui/src/lib/queryView.ts` exports `toCsv(rows: QueueRow[])`,
called from `apps/planner-ui/src/App.tsx`'s `onExport` handler
(`downloadCsv(\`trax-io-${p.tab}.csv\`, toCsv(view))`). It is entirely
client-side: `view` is the already-loaded, already-filtered set of rows for
the active tab (`planner-ui` loads its whole queue into the browser and
filters there — a model that does not scale to `apps/web`'s ~40k-SKU
portfolio, which is why `apps/web`'s Workbench is deliberately server-paged
at `MAX_PAGE_SIZE = 200`; see `apps/web/src/features/workbench/queueView.ts`).

The exact columns, in order (`CSV_COLUMNS` in `queryView.ts:84-99`):
`recommendation_id, pn, location, description, type, tier, criticality_tier,
aog_risk_level, confidence_score, recommended_quantity,
estimated_cost_impact, priority_score, status, reason`.

Every one of these fields already exists on the BFF's `QueueRow` model
(`services/agent-spine/src/trax_io_spine/bff/models.py:45-64`) — no new data
needs to be computed, only re-serialized differently.

## Design

### Why not just port `planner-ui`'s client-side approach

`apps/web`'s Workbench never loads the full queue into the browser — it is
server-paged specifically because the portfolio can be ~40k SKUs. Exporting
"the current page" would only cover ≤200 rows, silently dropping everything
else that matches the user's filters. The user confirmed (this session) the
export must cover the **full filtered result set**, not just the loaded
page — so this needs a server-side export, not a client-side one like
`planner-ui`'s.

### Backend: `services/agent-spine`

**New route**, alongside the existing queue route in
`src/trax_io_spine/bff/app.py` (the existing route is at line 65-98):

```
GET /v1/tenants/{tenant_id}/recommendations/export.csv
```

Query params: `status` (default `TaskStatus.PENDING`, same as the existing
queue route), `sort_by`, `sort_dir`, `tier`, `type`, `aog_min` — identical
types/defaults to the existing `queue()` route's params
(`app.py:66-80`) — but **no `limit`/`offset`**: this route always returns
every matching row.

**New `PlannerStore` method**, `list_queue_all`, in
`src/trax_io_spine/bff/store.py` alongside `list_queue_page`
(store.py:446-474). `list_queue_page` already separates filtering/sorting
(`self._sorted_entries(...)`) from pagination (`entries[offset:offset+limit]`)
— `list_queue_all` takes the identical keyword arguments minus `limit`/
`offset`, calls `self._sorted_entries(...)` the same way, and returns
`[self._row(e) for e in entries]` with no slicing. This reuses the store's
existing filter/sort logic verbatim; it does not duplicate it.

**CSV serialization**: use Python's stdlib `csv` module
(`csv.writer(io.StringIO(), quoting=csv.QUOTE_ALL)`) — no new dependency.
Column order and names are the same 14 columns `planner-ui` uses, listed
above, read off each `QueueRow`. The route returns a FastAPI `Response` with
`media_type="text/csv"` and header
`Content-Disposition: attachment; filename="trax-io-{status}-recommendations.csv"`
(e.g. `trax-io-pending-recommendations.csv`) — the `Content-Disposition:
attachment` header is what makes a browser download the file directly on
navigation, rather than rendering it, with no client-side Blob/JS needed.

### Frontend: `apps/web`

**`bffClient`** (`src/lib/api/client.ts`) gains one new pure function (not a
`request<T>()` call, since this isn't JSON): `recommendationsExportUrl(params, tenant)`,
building the same query string `getQueue` already builds
(`client.ts:108-117`'s `URLSearchParams` pattern) against the new
`/recommendations/export.csv` path, and returning the full URL string. No
fetch happens in the client — see below.

**Trigger mechanism**: a real `<a href={exportUrl}>Export CSV</a>` element
(styled as a button via the existing `Button` component's `asChild` pattern,
matching shadcn/ui conventions already used elsewhere in this codebase — e.g.
`src/features/feeds/PartStatSheetLookup.tsx`'s navigation links), not a
`fetch()`-triggered blob download. Because the response carries
`Content-Disposition: attachment`, a plain browser navigation to the URL
downloads the file without leaving the current page — no JS click-handler,
Blob URL, or `URL.createObjectURL` needed. This also sidesteps the
already-known, separately-tracked CORS gap in the standalone dev workflow
(`apps/web`'s dev server calling the BFF directly): a browser-initiated
navigation to a cross-origin URL is not subject to CORS preflight the way a
`fetch()`/XHR call is, so the export works in every environment including the
one where `apps/web`'s own `fetch()` calls currently do not.

**Two call sites:**

1. **Workbench** (`src/features/workbench/Workbench.tsx`) — the "Export CSV"
   link sits in the toolbar area alongside the existing tier/type/AOG filter
   pills and bulk-action buttons. Its `href` is computed from whatever filter
   state Workbench currently holds (the same `status`/`tier`/`type`/`aog_min`/
   `sort_by`/`sort_dir` values already driving its `useQueue(...)` call) — so
   the exported file always matches what's currently filtered on screen,
   independent of pagination.
2. **AI Recommendations** (`src/features/recommendations/AiRecommendations.tsx`)
   — this view has no filter/sort UI at all; it always shows a fixed
   `useQueue("pending", 50, 0)` sliced to the top 10 cards
   (`CARD_LIMIT = 10`, `AiRecommendations.tsx:11`). Its "Export CSV" link
   uses `status=pending` and no other params — exporting the **full** pending
   queue (reusing the same backend route with different fixed params), not
   just the 10 displayed cards, since a 10-row export would be a
   confusingly small file relative to what a user would expect an "export"
   button to produce.

## Testing

**Backend** (`services/agent-spine/tests/bff/`):
- `list_queue_all` returns every entry matching a filter combination, in the
  same order `list_queue_page` would page through (same sort applied,
  confirmed by comparing against `list_queue_page` with a `limit` larger
  than the fixture's total entry count).
- The route: a 200 response with `Content-Type: text/csv`, the correct
  `Content-Disposition` header, and a body whose parsed CSV (via Python's
  `csv.reader`) has the exact 14-column header and one data row per matching
  entry — including a case with `tier`/`type`/`aog_min` filters applied,
  confirming the export narrows exactly like the paginated endpoint does.
- A row containing a comma and a double-quote in its `reason` or
  `description` field round-trips correctly through `csv.reader` (quoting
  correctness, not just column presence).

**Frontend** (`apps/web/src/features/workbench/`,
`src/features/recommendations/`):
- `recommendationsExportUrl` builds the expected query string for a given
  filter/sort combination, and omits params that are unset (mirroring
  `getQueue`'s existing param-omission pattern).
- Workbench renders the Export CSV link with an `href` reflecting the
  currently-active filters (assert the URL's query string changes when a
  filter pill is toggled).
- AI Recommendations renders the Export CSV link with `href` fixed to
  `status=pending` regardless of what's displayed.

## Out of scope

- Any change to `apps/planner-ui` — it keeps its own client-side export
  unchanged.
- Free-text search is not part of the export's filter set, matching the
  existing paginated queue endpoint's own documented scope (search stays
  client-side over the loaded page; see `app.py`'s comment at line 82-83).
- Exporting from any other `apps/web` view (Overview, Forecast, Scenarios,
  Data & Connections) — not requested, and none of them show a
  recommendations queue.
