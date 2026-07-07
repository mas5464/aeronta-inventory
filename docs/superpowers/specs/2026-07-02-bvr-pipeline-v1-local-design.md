# Business Value Report Pipeline (v1-local) — Design Spec

**Date:** 2026-07-02
**Owner:** Miguel Sosa
**Status:** Approved (design).
**Sub-project:** #8 (Wave 2 exit criterion). Adapts
[docs/plans/2026-04-14-bvr-pipeline-plan.md](../../plans/2026-04-14-bvr-pipeline-plan.md)
(April, AWS-flavored, multi-month assumptions) to today's deterministic local
pipeline. Design refs: design §7.2–7.3 (savings attribution, monthly BVR —
"the contract-renewal engine"), §5.5 (service-level targets per essentiality
tier).

## Goal

A **generated, schema-locked Business Value Report** per tenant, computed
deterministically from what the deployed system already knows, rendered as a
Trax-styled printable document (HTML always; PDF via optional extra), and
**auto-posted to the web frontend** (a live Reports section). First BVR delivered
over the real 58.9K-key full-network deploy.

## The honesty contract (owner decision, 2026-07-02)

Only **one extract snapshot** exists (2024-04-01) and no policy change has
lived in production — so v1-local reports **projected** value, never
"realized"/"saved":

- The **pre-agent baseline is real**: the extract's own `CurrentPolicy` is the
  genuine pre-agent `PN_INVENTORY_LEVEL` state, and the writeback ledger
  (`HistoryEntry`, incl. `SHADOWED`) records exactly what the agent changed
  (or would have) — `old_values → new_values`, tier, principal, timestamps.
- Every monetary figure is labeled **projected**, decomposed with disclosed
  conservative formulas, and traceable to its inputs.
- **Realized-vs-counterfactual attribution is explicitly out of scope** until
  sequential monthly extracts exist; the spec states what unlocks it (§Out of
  scope).

## Architecture (approach A, chosen)

A new `trax_io_spine.bvr` package inside `services/agent-spine` — computed off
the same `PlannerStore` the UI trusts (retained feature store + `_entries`
lifecycle + writeback ledger). No new service; no scheduler locally (the report
regenerates from the live store; monthly cadence arrives with monthly
extracts). Rejected: a standalone `services/bvr-pipeline` package (duplicated
loading, plumbing without local benefit) and a frontend-computed report (the
contract-renewal artifact must be server-authoritative).

### 1. Schema — `bvr/models.py` (the "BVR schema locked" deliverable)

Frozen pydantic models (`_Base` convention: `frozen=True, extra="forbid"`),
`BvrReport.schema_version = "1.0.0"` (semver; additive → minor):

- `BvrPeriod` — `extract_date` (the snapshot), `decision_window_start/end`
  (min/max `changed_at` over ledger entries; `None` when no writes),
  `generated_at`, `label` (e.g. "Snapshot 2024-04-01").
- `ExecutiveSummary` — hero numbers: total projected value, changes applied /
  shadowed, keys under management, open-pipeline value, service-posture
  headline.
- `ProjectedComponent` — `name`, `amount: Decimal`, `formula` (human-readable,
  e.g. "Δ(safety_stock + EOQ/2) × unit_cost × holding_rate × period_fraction"),
  `inputs` (dict of the counts/rates used), `assumptions` (list of disclosed
  strings).
- `SavingsAttribution` — `holding_cost_delta`, `ordering_cost_delta`,
  `stockout_risk_delta` (each a `ProjectedComponent`), and an explicit
  applied/shadowed split: `total_projected_applied` (WRITTEN entries),
  `total_projected_shadowed` (SHADOWED entries), `total_projected` (their
  sum) — a shadowed would-be write is never silently blended into the
  applied figure. Plus `changes_total` / `changes_valued` (the "N of M
  valued" coverage disclosure) and `assumption_rates` (holding_cost_rate,
  per_order_cost, stockout proxy fraction).
- `ServicePosture` — per essentiality tier 1–5: the §5.5 fill-rate target vs
  the **posture** metric (share of that tier's keys where current ROP ≥ mean
  lead-time demand, i.e. `rop ≥ demand_rate_per_day × lead_time_mean_days` —
  reusing the demand/lead-time primitives the forecast machinery already
  derives), labeled posture-not-realized.
- `Governance` — recommendation totals by lifecycle (pending / approved /
  rejected / deferred), approval rate, override (reject) rate, rollback count
  and shadowed count from the ledger, tier mix of writes, kill-switch state.
- `ForwardLook` — open-pipeline value (pending recs' `estimated_cost_impact`
  sum), projected demand over the horizon, top-**10** pending opportunities
  (pn/location/type/impact, impact-ranked).
- `Methodology` — formulas + assumption rates restated, input counts
  (ledger entries, recs, keys), `input_snapshot_hash`es seen, agent/schema
  versions. This is the appendix that makes every number auditable.

### 2. Attribution engine — `bvr/attribution.py`

Pure, deterministic functions; unit-tested against hand-computed fixtures.
Inputs: ledger entries (`WRITTEN` + `SHADOWED`), a `FeatureStoreClient`, the
store's `_entries`. Per change, baseline policy = `old_values` (or the
extract `CurrentPolicy` for a first write with `old_values=None`); new =
`new_values`. Unit cost = `VendorEconomics` `DEFAULT` vendor row; annualized
demand from the key's `DemandHistory` (24-month window ÷ 2).

- **Holding-cost delta** = Δ(safety_stock + EOQ/2) × unit_cost ×
  `holding_cost_rate` (default **0.25/yr**, parameterized) ×
  `period_fraction` (default 1/12 — a monthly-shaped report).
- **Ordering-frequency delta** = (annual_demand/EOQ_old −
  annual_demand/EOQ_new) × `per_order_cost` (default **$85**, parameterized)
  × period_fraction. EOQ ≤ 0 on either side ⇒ component skipped for that key
  (counted).
- **Stockout-risk delta** (conservative proxy, no AOG monetization):
  Δ(units of lead-time demand covered at ROP, floored at 0) ×
  unit_cost × `stockout_proxy_fraction` (default **0.10**, parameterized) ×
  tier weight (tiers 1–5 → 1.0 / 0.8 / 0.6 / 0.4 / 0.2) × period_fraction.
- A change whose unit cost is unavailable is **counted, not silently
  dropped** (`changes_valued < changes_total` disclosed in the report).
- Signs: positive amount = projected benefit; reductions in stock with
  worsened coverage can produce negative stockout-risk components — reported
  as-is, never clamped.

### 3. Renderer — `bvr/render.py` + `bvr/templates/bvr.html.j2` + `bvr/svg.py`

- New agent-spine extra `bvr = ["jinja2>=3.1"]`. `render_html(report) -> str`:
  a single self-contained HTML document (embedded CSS, print-optimized,
  Trax-styled header/footers), sections mirroring the April plan: executive
  summary → savings attribution → service posture → governance → forward look
  → methodology appendix.
- Charts are **inline SVG built by small pure helpers** in `bvr/svg.py`
  (h-bar breakdown of the three components; tier posture bars; lifecycle
  donut) — deterministic, print-safe, no JS. The April plan's Chart.js +
  headless-Chromium (`pyppeteer`) stack is **deliberately dropped**.
- `bvr/pdf.py` behind extra `pdf = ["weasyprint>=61"]`:
  `render_pdf(html) -> bytes`. Probe-verified locally (WeasyPrint 69) — on
  macOS requires `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` (homebrew
  pango/cairo); the Docker BFF image gains apt `libpango-1.0-0
  libpangocairo-1.0-0` (+deps). Tests **skip cleanly** when the native libs
  can't load (same pattern as the iceberg/dynamodb extras).

### 4. BFF surface — `bff/store.py` + `bff/app.py`

- `PlannerStore.bvr() -> BvrReport` — built on first call, memoized
  (`_bvr_cache`, the `_key_stats_cache` pattern; decision actions invalidate
  it so the report always reflects the current lifecycle state).
- Routes:
  - `GET /v1/tenants/{tenant}/reports/bvr` → JSON `BvrReport`.
  - `GET /v1/tenants/{tenant}/reports/bvr.html` → `text/html` (the printable
    document).
  - `GET /v1/tenants/{tenant}/reports/bvr.pdf` → `application/pdf`, or **501**
    with a clear body when the `pdf` extra isn't installed.
- Read-only; unaffected by the kill switch. Auto-post is intrinsic: the
  report exists at a stable URL as soon as the store is seeded.

### 5. Planner-UI Reports section — `apps/planner-ui`

- NavRail gains a **live "Reports"** item → `#/reports` (HashRouter route,
  same pattern as `#/dashboard`).
- `ReportsView`: hero tiles (total projected value, changes applied/shadowed,
  approval rate, service-posture headline) + the savings decomposition and
  governance strips rendered from the JSON; buttons **"Open printable
  report"** (same-origin `/v1/...bvr.html`, new tab) and **"Download PDF"**
  (`.pdf`; surfaced error message when the BFF answers 501).
- Typed client: `getBvr(tenant) -> BvrReport` TS mirror; `FakePlannerClient`
  gains sample data. `apps/web` untouched this slice.

### 6. Testing

- **Attribution fixtures**: hand-computed expected values for each component
  (incl. first-write baseline from `CurrentPolicy`, missing-vendor coverage
  counting, negative component, EOQ=0 skip).
- **Schema lock**: a test snapshotting `BvrReport`'s field set +
  `schema_version` (additive changes must bump minor).
- **Determinism**: same store ⇒ identical report modulo `generated_at`.
- **Render smoke**: HTML contains the hero numbers, all six section
  headings, and well-formed inline `<svg>`; no external resource URLs.
- **PDF**: skip-gated round-trip (`%PDF` magic + nonzero size).
- **BFF routes**: JSON shape, HTML content-type, `.pdf` 501-when-absent,
  tenant isolation (existing two-tenant pattern), cache invalidation on
  approve.
- **planner-ui**: Vitest for ReportsView tiles/links + NavRail live item +
  fake-client route; UAT.md section added. All existing suites stay green.

## Out of scope (v1-local; each names its unlock)

- **Realized-vs-counterfactual attribution** — unlocked by sequential monthly
  extracts (≥2 periods): then baseline trajectory vs actual
  `PN_INVENTORY_LEVEL`/stock/orders can be computed per the April plan's
  Phases 1–3. The `BvrReport` schema reserves nothing for it; it will arrive
  as new sections under a minor version bump.
- **PDF/A-3 archival profile + tagged-PDF accessibility audit** — plain
  WeasyPrint PDF now; archival/compliance profile with the AWS delivery
  phase (S3 Object Lock).
- **Scheduler/delivery infra** (EventBridge day-3 rule, S3, e-mail) — local
  reports regenerate from the live store on demand.
- **Trax Ops dashboard (Phase 9), A/B report design (Phase 10), CFO sign-off
  workflow (Phase 0)** — organizational, not local-buildable.
- **apps/web Reports view** — planner-ui first (owner decision); the JSON
  endpoint is UI-agnostic for a later apps/web slice.

## Risks

- **Proxy honesty**: holding/ordering/stockout formulas are simplifications.
  Mitigation: conservative defaults, every formula + rate disclosed in the
  report body and methodology appendix, "projected" labeling throughout, and
  parameterized rates so onboarding can calibrate per tenant.
- **Coverage gaps**: keys without `DEFAULT` vendor economics can't be valued.
  Mitigation: explicit `changes_valued < changes_total` disclosure; never
  silently dropped.
- **WeasyPrint native deps**: env-specific. Mitigation: optional extra,
  skip-clean tests, macOS env var documented, Docker apt install pinned in
  the Dockerfile.
- **Report staleness vs lifecycle**: memoized report could go stale after
  approves. Mitigation: decision actions invalidate `_bvr_cache` (tested).
- **At 58.9K keys** the attribution pass is O(ledger entries + keys) — the
  ledger is small locally; the posture scan reuses the existing forecast
  classification approach (already proven at this scale).
