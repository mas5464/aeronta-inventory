# apps/web: Reports / Business Value Report view (Feature-Parity Wave 3 of 4)

## Context

Wave 3 of the four-part effort bringing `apps/web` ("Trax Inventory
Optimizer") to feature parity with `apps/planner-ui`, ahead of retiring
planner-ui. Waves 1 (CSV export) and 2 (writeback history + rollback) shipped;
Wave 4 (dark/light theme) is the remaining follow-up.

`apps/web` has **no reporting route today**. `apps/planner-ui` has a full
`ReportsView` rendering the #8 Business Value Report (BVR) — hero tiles,
savings decomposition, governance strip, printable HTML + PDF links — over the
BFF's `/reports/bvr` endpoints. This wave brings the same capability to
`apps/web` over the **same BFF** — no backend change; the routes already exist.

Standing constraint (confirmed across the parity effort): `apps/web` must
remain embeddable in eMRO later (iframe/module). Nothing here requires new work
toward that (HashRouter already in use).

## What already exists (verified against source + live)

**BFF endpoints** (`services/agent-spine/src/trax_io_spine/bff/app.py:206-216`):
- `GET /v1/tenants/{tenant_id}/reports/bvr` → `BvrReport` (JSON).
- `GET .../reports/bvr.html` → printable HTML document (`text/html`).
- `GET .../reports/bvr.pdf` → PDF (WeasyPrint). **The deployed BFF Docker image
  includes the `pdf` extra** (`deploy/bff.Dockerfile:23`: `uv sync --extra bff
  --extra bvr --extra pdf`), and all three endpoints return **200** live
  through the nginx same-origin proxy (verified) — so both document links work.

**`BvrReport` shape** — a rich report; the full pydantic contract is mirrored
field-for-field in `apps/planner-ui/src/api/types.ts:290-378` (`BvrReport`,
`BvrSavings`, `BvrGovernance`, `TierPosture`, `ProjectedComponent`). Its top
level: `schema_version`, `tenant_id`, `period {extract_date,
decision_window_start/end, generated_at, label}`, `executive_summary
{total_projected, changes_applied, changes_shadowed, keys_under_management,
open_pipeline_value, service_headline}`, `savings` (3 `ProjectedComponent`s +
`total_projected_applied/shadowed/total`, `changes_total/valued`,
`assumption_rates`), `service_posture {tiers[], note}`, `governance`
(recommendation counts, `approval_rate`, `override_rate`, write counts,
`rollbacks`, `tier_mix`, `kill_switch_engaged`), `forward_look
{open_pipeline_value, projected_demand_horizon, top_opportunities[]}`,
`methodology {formulas[], assumption_rates, ledger_entries, recommendations,
keys, keys_total_portfolio, input_snapshot_hashes[],
input_snapshot_hash_count, agent_version, generated_by}`. A `ProjectedComponent`
is `{name, amount (Decimal serialized as string), formula, inputs, assumptions[]}`.

**planner-ui's `ReportsView`** (`apps/planner-ui/src/components/ReportsView.tsx`)
is the reference layout: header ("Business Value Report" + period meta + a
"projected vs pre-agent baseline" caveat badge), executive-summary tiles,
savings decomposition (`<ul>` of components with `$amount`), governance strip
(recommendations total · approval rate · override rate · rollbacks), and the
two document links ("Open printable report" / "Download PDF").

**apps/web patterns to reuse** (verified): `NAV_ITEMS` array + `<Route>` in
`App.tsx` (6 items today; nav uses `NavLink` with auto `aria-current`); the
shared `<QueryState>` (loading/error+Retry/empty); the `bvrDocumentUrl`-style
"real `<a href>` browser navigation" pattern proven in Wave 1's CSV export
(sidesteps the standalone-dev CORS gap); `useQuery` with `staleTime: 60_000`
on read-heavy queries; the existing `/parts/:pn/:location` link pattern
(`Overview`, `Workbench`) for the forward-look top opportunities.

## Design

### Provenance-invariant boundary (decided)

`apps/web` normally renders every metric through `Metric`/`ProvChip` with a
`MetricValue` lineage. The Reports view is the **deliberate, documented
exception**: the BVR is a self-describing formal *report document*, not an
operational dashboard of independently-sourced metrics. It carries its own,
richer provenance inline — `period.generated_at`, `schema_version`,
`methodology.agent_version`/`generated_by`, and a full `methodology` section
(formulas, assumption rates, keys-valued-vs-`keys_total_portfolio`, snapshot
hashes). So the report renders WITHOUT per-number `ProvChip`s, and the
**methodology section IS the report's provenance disclosure**. This mirrors the
same boundary already set in Wave 2 (writeback-history rows are audit events,
not `MetricValue`s) and matches planner-ui's own treatment. No `Metric`/
`ProvChip`/`withProvenance`/`MetricValue` usage in the Reports view.

### Data layer — `apps/web/src/lib/api/`

**`types.ts`** gains the BVR types, copied field-for-field from planner-ui's
already-correct mirror of the same pydantic contract: `ProjectedComponent`,
`BvrSavings`, `TierPosture`, `BvrGovernance`, and `BvrReport` (with its inlined
`period`/`executive_summary`/`service_posture`/`forward_look`/`methodology`
object shapes). `amount`/`total_*` fields are `string` (Decimal serialized by
the BFF); counts/rates are `number`.

**`client.ts`** gains:
- `getBvr(tenant = DEFAULT_TENANT): Promise<BvrReport>` → `GET .../reports/bvr`
  via the existing `request<T>` helper.
- `bvrDocumentUrl(tenant = DEFAULT_TENANT, kind: "html" | "pdf"): string` — a
  pure URL builder returning `${BASE_URL}/v1/tenants/{tenant}/reports/bvr.{kind}`
  (consumed as an `<a href>`, browser navigation — same pattern/rationale as
  `recommendationsExportUrl` from Wave 1; not a `fetch()`).

**`useBvr.ts`** (new): `bvrQueryKey(tenant)` = `["bvr", tenant]`; `useBvr(tenant?)`
= `useQuery<BvrReport>` on that key with `staleTime: 60_000` (matching the other
read-heavy queries; a report snapshot doesn't need refetch-on-every-mount).

### The `/reports` view — `apps/web/src/features/reports/`

A new `Reports` page component (route `/reports`, added to `NAV_ITEMS` as
"Reports" — the 7th item — and a `<Route path="/reports" element={<Reports />} />`).
It calls `useBvr()` and renders through `<QueryState>` (loading label "Loading
Business Value Report…"; error + Retry via `refetch`; the BVR is always
present for a valid tenant, so a dedicated empty state isn't required, but the
error path covers a failed build — the BFF maps an internal failure to a clean
500, so `<QueryError>` surfaces it).

Sections (mirroring planner-ui's ReportsView, in apps/web's Tailwind/`Card`
vocabulary):
1. **Header** — "Business Value Report" `<h1>`, the `period.label`,
   `generated_at` (formatted), a caveat badge ("Projected vs pre-agent
   baseline"), and `schema_version` / `methodology.agent_version` as small meta.
2. **Executive summary** — tiles for `total_projected` (currency),
   `changes_applied`, `changes_shadowed`, `keys_under_management`,
   `open_pipeline_value` (currency), and the `service_headline` text.
3. **Savings (projected)** — the three `ProjectedComponent`s
   (`holding_cost_delta` / `ordering_cost_delta` / `stockout_risk_delta`) as a
   list: human-readable label + `$amount` + the `formula` string; plus the
   applied/shadowed/total split. **Component `name` is the raw snake_case key**
   (verified live: `name: "holding_cost_delta"`, `amount: "-0.06"`), so the
   view MUST map it through a small display-name map
   (`holding_cost_delta` → "Holding cost", `ordering_cost_delta` → "Ordering
   cost", `stockout_risk_delta` → "Stockout risk"), with a title-cased fallback
   for any unknown key — NOT render `name` raw. This is a deliberate
   improvement over planner-ui, which rendered `c.name` directly (the
   raw-`snake_case`-to-users issue flagged in this session's UX audit, PUI-I2).
4. **Governance** — recommendations total, approval rate, override rate,
   rollbacks, write counts, and a kill-switch indicator (the `bad`/`warn`
   variant when engaged — text + color, not color-alone).
5. **Forward look** — `open_pipeline_value`, `projected_demand_horizon`, and a
   `top_opportunities` list, each row linking to
   `/parts/{pn}/{location}` (reusing the existing part-link pattern; encode
   the segments).
6. **Methodology** — the formulas, `keys` of `keys_total_portfolio` (the valued
   vs. full-portfolio disclosure), assumption rates, `input_snapshot_hash_count`,
   `agent_version`, `generated_by`. Framed as the report's provenance section.
7. **Document links** — "Open printable report" → `bvrDocumentUrl(tenant,
   "html")` and "Download PDF" → `bvrDocumentUrl(tenant, "pdf")`, both real
   `<a href>` (target `_blank` for HTML; the PDF's `Content-Disposition` drives
   the download). Both verified working live.

**Currency formatting:** `amount`/`total_*`/`open_pipeline_value` are
Decimal-serialized **strings** already formatted server-side — display with a
`$` prefix (as planner-ui does), NOT through `Intl.NumberFormat` on a parsed
float (avoids the float-precision class of bug the earlier audit found in the
integer formatter). Counts/rates are numbers: format rates as `(rate *
100).toFixed(1)%`.

## Testing

**Client** (`client.test.ts`): `getBvr` builds `.../reports/bvr` and returns the
parsed report; `bvrDocumentUrl(tenant, "html"|"pdf")` returns the exact
`.../reports/bvr.html` / `.pdf` URLs; `getBvr` surfaces a non-OK response as
`ApiError`.

**Hook** (`useBvr.test.tsx`): `bvrQueryKey` shape; `useBvr` fetches and returns
the report (spy on `bffClient.getBvr`). Use `vi.restoreAllMocks()` in
`afterEach` (per the Wave 2 PR-review lesson — spies must not leak across
tests).

**`Reports` view** (`Reports.test.tsx`): renders every section from a mocked
`BvrReport` fixture — header + period label, all exec-summary tiles, the three
savings components with amounts, governance rates, forward-look opportunities
each linking to the correct `/parts/...` href, methodology's keys-of-portfolio
disclosure; the HTML/PDF links carry the correct hrefs; loading and
error(+Retry) states via `<QueryState>`. Assert NO `ProvChip` is rendered
(the documented boundary — e.g. query by the ProvChip's test id / role and
expect none).

**Live Docker verification** (rebuild web; BFF unchanged): at
`http://localhost:8089`, the "Reports" nav item opens `/reports`; the BVR
renders with real network-scale figures; the printable-HTML and PDF links open
the BFF-served documents; a forward-look opportunity link lands on the correct
Part Drill-Down.

## Out of scope

- Any `apps/planner-ui` change — it keeps its own `ReportsView`.
- Any BFF change — the `/reports/bvr*` routes already exist and are unchanged.
- Wave 4 (dark/light theme).
- Editing/regenerating the report, date-range selection, or historical report
  archives — the BFF serves a single current memoized report; matching that.
- Rendering the BVR through the `Metric`/`ProvChip` provenance primitives —
  explicitly excluded per the documented boundary above.
