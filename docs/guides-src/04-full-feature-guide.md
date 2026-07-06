---
title: "Trax IO — Full Feature Guide"
subtitle: "What Actually Runs Today: v1 Reference Implementation Walkthrough"
author: "Miguel Sosa, VP Head of Innovation · Trax"
date: "2026-07-06"
---

\newpage

# 1. Purpose and How to Read This Guide

The Technical Architecture Guide (dated 2026-04-14) describes the *target* AWS-native
design — Bedrock AgentCore, Strands, SageMaker, Glue + Iceberg, DynamoDB. It was written
before a line of implementation existed. This guide is its companion for what's true
today: a substantial, independently-tested **local reference implementation** that
proves the entire recommendation lifecycle end-to-end — extract, feature store,
forecasting, deterministic policy engine, guardrails, audited writeback, and two
full React frontends — without yet touching AWS.

Read this guide to understand what the product actually does and how a user or
reviewer can exercise it today. Read the **AWS Infrastructure Guide** for what
target-state cloud footprint the design calls for, what's already written as
CDK infrastructure-as-code, and what remains before any of it deploys.

**One sentence on why this exists:** building the real decision logic, guardrails,
and both UIs first — in plain Python and Docker — lets the product be reviewed,
UAT-tested, and demoed months before an AWS deployment pipeline exists, and de-risks
the AWS migration to an infrastructure problem rather than a logic problem.

\newpage

# 2. System Map

Nine independently-tested packages, wired by four `uv` path dependencies and two
HTTP-consuming frontends. Everything runs on a laptop or in the project's own
Docker Compose stack — nothing here requires an AWS account.

| Package | Stack | Role |
|---|---|---|
| `tools/nightly-extract` | Python CLI | 21 canonical SQL domains against eMRO Oracle; offline sample mode |
| `services/feature-store` | Python + pydantic | `FeatureStoreClient` Protocol; `InMemoryFeatureStore` reference impl; 11 feature-group schemas |
| `services/recommendation-engine` | Python | Context assembly, regime-routed forecasting, deterministic policy + guardrails |
| `services/forecasting` | Python (statsforecast, sklearn, scipy) | 3 regime-specific projector slices behind one interface |
| `services/event-publisher` | Python | Canonical event schema + conformance harness for the eMRO event contract |
| `services/agent-spine` | Python + FastAPI | Orchestration CLI, event ingestion, Cedar autonomy policy, audited writeback, Planner-UI BFF, BVR reports |
| `apps/planner-ui` | React 18 + TS + Vite | "Trax IO Review" — the ops-console approval queue |
| `apps/web` | React 18 + TS + Tailwind | "Trax Inventory Optimizer" — the full 7-view PRD-faithful app |
| `infra/feature-store`, `infra/observability-soc2` | AWS CDK (Python) | Synth-tested infrastructure-as-code — see the AWS Infrastructure Guide |

**Real data flow, today:** a nightly extract directory (either the 21-domain sample
fixture or a real Oracle-shaped export) seeds an in-memory feature store. The
recommendation engine reads through a `FeatureReader`/`ContextAssembler` pair to
build a full `PartLocationContext` per `(pn, location)`, runs it through
regime-routed forecasting and the deterministic policy engine, and emits a
`Recommendation` with guardrail-checked autonomy tier and full provenance. The
agent-spine orchestrates this, applies writes through an audited, rollback-capable
writeback target, and serves everything to both frontends over one FastAPI BFF.
No LLM, no agent framework, and no cloud service sits in this path today — it is
pure, fast, fully-tested Python, deliberately built behind the same interfaces
the AWS-native version will implement (see ADR-0002, ADR-0003).

\newpage

# 3. Nightly Extract & Data Ingestion

`tools/nightly-extract` implements the 21 canonical SQL domains against eMRO's
Oracle schema — the single source of truth for every extract query is
`~/Downloads/PTC Project Files/eMRO Data SQLs.sql`, with legacy `PKG_TRAX_PTC`
PL/SQL logic (`getKitCost`, `getRecordsType`) inlined directly into the SQL so v1
has no package dependency. An extract manifest contract
(`docs/contracts/2026-04-17-extract-manifest-contract.md`) governs the
SHA-256-checked handoff between an extract run and everything downstream.

Two ways to get an extract directory today: a real Oracle connection (Phase 2,
gated behind live database access), or the checked-in sample fixture
(`services/recommendation-engine/examples/extract_sample`, ~21,215 keys) used for
every UAT and demo path in this guide. The full local Docker stack instead mounts
a **complete, real, eMRO-shaped 58.9K-key extract snapshot**
(`deploy/_local_extract/emro_net_full_snapshot`, gitignored, regenerated via the
`trax-io-precompute` entrypoint) — the closest thing to production scale this
implementation exercises.

\newpage

# 4. Feature Store

`services/feature-store` defines the `FeatureStoreClient` Protocol and an
`InMemoryFeatureStore` reference implementation (ADR-0002) — the production
`GlueIcebergFeatureStore` will conform to the identical Protocol, so every
consumer above it is already forward-compatible with the AWS-native layer. A
shared contract test suite runs the same scenarios against both.

Eleven feature-group schemas are implemented, matching design §4.2's ten plus one
addition from this build's own Wave 4 work:

`part_attributes` · `criticality` · `stock_position` · `current_policy` ·
`vendor_economics` · `lead_time_distribution` · `demand_history` ·
`interchangeable_graph` · `location_graph` · `open_orders_snapshot` ·
**`requisition_snapshot`** (added for the Fulfillment-Path Decision Agent's
Wave A — open, unfulfilled demand-side requisition lines per `(pn, location)`,
deliberately kept separate from `open_orders_snapshot`'s supply-side data).

113 tests cover the client, schemas, snapshot registry, Glue transform logic, and
(with the `iceberg`/`dynamodb` extras) contract-equivalence and online-layer tests
that will matter once the real AWS backends land.

\newpage

# 5. Recommendation Engine

`services/recommendation-engine` is the deterministic core planners actually sign
off on. `ContextAssembler` and `FeatureReader` pull every feature-store bucket
needed for one `(tenant, pn, location)` into a single `PartLocationContext`
(attributes, stock, policy, vendor economics, lead time, demand history,
interchangeability/location graphs, open orders, and now requisitions).

**Regime-routed forecasting**, three independently-tested slices behind one
`DemandProjector` interface:

| Slice | Regime | Method |
|---|---|---|
| A — Statistical | `intermittent` | `statsforecast` (Croston/TSB/SBA family) |
| B — Gradient-boosted | `moderate`, `high_volume` | sklearn `HistGradientBoosting` |
| C — Empirical Bayes | `ultra_rare` | Gamma-Poisson peer-prior model, pure numpy/scipy |

**Deterministic policy engine** — no LLM anywhere in this path — computes
`(ROP, EOQ, Safety Stock, Max)` from the forecast distribution, lead-time
distribution, and cost structure, then runs every hard guardrail from design §6.2:
single-write delta capped at 100% even in Tier C, shelf-life/hazmat/tool-control
clamps, and an active AOG forcing Tier A regardless of any other criterion. A
`humanize_guardrail_codes()` layer translates internal guardrail codes
(`active_aog`, `shelf_life_clamped`, `hazmat_tool_capped`, `open_order_deferral`,
`delta_exceeds_100pct`) into planner-readable notes — the recommendation's own
`reason` text is never overwritten by these, only supplemented.

149 tests cover context assembly, extract loading, the policy engine, and (with
the `api` extra) the package's own FastAPI surface.

\newpage

# 6. Agent Spine — Orchestration, Autonomy, Writeback

`services/agent-spine` is the coordination layer — 282 tests, the largest package
in the repo. It ships the `trax-io-spine` CLI with two commands:

- **`run`** — offline orchestration: extract → recommendation engine → guardrail
  → writeback, printing an `OrchestrationResult` summary. A `--shadow` flag logs
  every would-be write as `SHADOWED` provenance without applying anything —
  the onboarding-mode building block for the design's 30–90 day shadow period.
- **`ingest`** — replays a JSONL batch of canonical events (dedup by `event_id` →
  canonical adapter → event-lane handler recompute → writeback) — the
  consumer-side proof of the eMRO event contract, independent of any real
  Kinesis/EventBridge infrastructure.

**Autonomy** is enforced as a `CedarAutonomyPolicy` — Cedar policy evaluation, not
hard-coded conditionals, matching the design's requirement that tenants tune
autonomy tiers without a code deploy.

**Writeback** sits behind an `AuditedWritebackTarget` Protocol (extends a base
`WritebackTarget` with `get_history` + `rollback`): a per-key provenance history
ledger, rollback over a configurable non-zero window (default 90 days), and
shadow-mode support. It is verified against `fake_emro` — itself backed by an
`InMemoryWritebackTarget` so the test double can never silently drift from the
real target's behavior (ADR-0003's contract-testing philosophy applied one layer
down).

\newpage

# 7. The Planner-UI BFF

One FastAPI application (`trax_io_spine.bff`) is the backend-for-frontend both
React apps consume — `PlannerStore` (in-memory, per-tenant, seeded from a real
extract via `from_extract` or a precomputed snapshot via `from_snapshot_dir`) plus
`create_planner_app`. Surface area:

- Priority-sorted approval queue with provenance detail
- Approve / reject / defer / bulk-approve, each producing a real writeback and a
  real history entry
- Writeback history + rollback per key
- Per-tenant kill switch (engaged ⇒ approvals return `423`)
- `GET /parts/{pn}/{location}` — full part context: attributes, stock breakdown,
  lead-time view, open orders, 24-month demand series
- `GET /dashboard` — portfolio KPIs and by-criticality/by-ATA/by-part-class/by-tier
  breakdowns, top shortages
- `GET /reports/bvr`, `.../bvr.html`, `.../bvr.pdf` — the Business Value Report
  (§10)

At the full 58.9K-key network, `from_snapshot_dir` boots in roughly 14 seconds with
no extract re-parsing — the difference between a usable local demo and a multi-minute
cold start. 266 BFF + agent-spine tests are green (`--extra bff --extra bvr`).

\newpage

# 8. Trax IO Review (`apps/planner-ui`) — The Ops Console

The first frontend built, and the one that has been through four dedicated visual
redesign phases. A typed `PlannerClient` (real HTTP or an offline `FakePlannerClient`
seeded with deterministic sample data) drives:

- **Pending / Decided tabs**, URL-routed (`HashRouter`, deep-linkable), WAI-ARIA
  tabs pattern with roving tabindex
- An ops-console shell: `NavRail`, a unified search/filter/CSV-export/bulk-approve
  `Toolbar`, `SummaryCards`, a by-type/by-tier `ChartRow`, and a dense, sortable
  `QueueTable`
- A right-side overlay **Drawer** (deep-linkable via `#/:tab/:id`) showing
  provenance detail, a **ConfidenceHero** card (tiered gradient percentage,
  "Key findings" evidence list, humanized guardrail notes), and — for approved
  rows — inline writeback history with one-click rollback
- A lazily-loaded **part-context drawer strip**: on-hand/serviceable/in-repair/need,
  lead-time, open orders, and a dependency-free inline-SVG demand trend chart with
  real-elapsed-time bar positioning and calendar gridlines
- A **Dashboard** view (`#/dashboard`) and a **Reports** view (`#/reports`)
  rendering the BVR
- User-toggleable dark/light theme (dark-first default), an automated,
  dependency-free WCAG contrast test suite (79 token pairs, tiered AAA/AA) gating
  every color decision in the palette

236 Vitest tests, `tsc` clean. A living `UAT.md` documents every manual case
against the automated-test map — the same document this session's full UAT pass
exercised end-to-end (see §12).

\newpage

# 9. Trax Inventory Optimizer (`apps/web`) — The Spec-Faithful App

A second, independently-built frontend rendering the full PRD §6 surface directly
over the same BFF — seven views: **Overview** (portfolio KPIs, health mix, ATA
risk, priority actions, in-place drill panels), **Part Drill-Down**, **Workbench**
(the server-paged core approval loop — search/filter/sort, accept/reject/dismiss,
bulk actions, a documented 200-row page-size ceiling with no virtualization
library), **AI Recommendations** (explainable rec → reason → action cards),
**Forecast & Service Levels**, **What-If Scenarios** (live-solved service-level /
budget / turnaround-time sliders, a cost–service frontier chart, save / compare /
commit with an honest "no eMRO writeback occurred" confirmation), and **Data &
Connections** (a 13-feed connection-status table — `CONNECTED` / `PARTIAL` /
`NOT_CONNECTED`, with honest per-feed notes rather than a decorative green light
everywhere).

The load-bearing design discipline here is the **provenance invariant**: every
displayed number is a typed `MetricValue` and cannot render without its
`{source, systemOfRecord, freshnessAt, coverage, confidence, derived}` lineage
attached — enforced at the type level, not by convention. A shared `<QueryState>`
helper standardizes loading/error/empty handling across all seven views, and a
dependency-free `useFocusTrap` hook backs every confirm dialog (reject, commit)
with tested Tab-wrap, Escape-close, and focus-restoration behavior.

231 Vitest tests, one best-effort Playwright e2e spec, build + lint clean.

\newpage

# 10. Business Value Report (BVR)

A schema-locked (`BvrReport 1.1.0`) JSON report, a printable Jinja2 + inline-SVG
HTML document, and a WeasyPrint-rendered PDF — all derived from one memoized,
decision-invalidated `PlannerStore.bvr()` call. It reports **projected-only**
savings attribution against the pre-agent baseline (holding cost, ordering cost,
stockout risk — each with disclosed inputs, including how many changes were
ordering-skipped due to non-positive EOQ), tier posture, governance, a forward
look, and a methodology section that explicitly discloses `keys_total_portfolio`
— the tenant's full `(pn, location)` universe — alongside however many keys were
actually valued, so the report never silently implies full-portfolio coverage
when some keys lacked demand history, criticality, vendor economics, or stock
position data.

\newpage

# 11. Local Full-Stack Deployment

`docker-compose.yml` at the repo root (project name `trax-io-planner`) runs three
services: **bff** (`:8001`, booting in ~14 seconds from the full snapshot),
**ui** (`apps/planner-ui` via nginx, `:8088`, reverse-proxying `/v1` to the BFF
same-origin), and **web** (`apps/web` via nginx, `:8089`). `docker compose up
--build` brings up the whole stack; each service is scoped to this project only
and never touches the shared `oracle19c` or MySQL containers used by other
projects on the same machine.

\newpage

# 12. Quality Posture

As verified in this session's full end-to-end UAT pass:

| Suite | Result |
|---|---|
| Backend (8 packages: nightly-extract, feature-store, recommendation-engine, agent-spine, forecasting, event-publisher, 2× infra) | **835 passed, 1 skipped** (the 1 skip is an Oracle-connection-gated test — expected), 0 failed. All `ruff check` clean. |
| `apps/planner-ui` | 238/238 Vitest, clean `tsc`/build. 76/79 live UAT.md cases pass; 2 flagged as documentation drift from an earlier redesign (not broken function). |
| `apps/web` | 231/231 Vitest, clean build/lint, 1/1 Playwright e2e. Live walkthrough found and fixed one real bug — see below — then re-verified clean, including live, state-changing verification of approve/reject/bulk-approve/kill-switch against the real running BFF. |

Both frontends carry living `UAT.md` documents mapping every manual case to the
automated test that already covers it, run before every release.

**One real bug found and fixed during this pass:** any query failure in `apps/web`
(a 404, a network error) left the affected view stuck on its loading state forever
instead of showing the existing `<QueryError>` UI's Retry button — the shared
`QueryClient`'s default `networkMode: 'online'` paused a scheduled retry
indefinitely because the browser's online/offline signal it depends on never
resolved. Every Vitest test constructs its own `QueryClient` with `retry: false`,
which is precisely why 231 green tests never caught it. Fixed in
`apps/web/src/main.tsx` (`retry: false` — every view's manual Retry button was
already the real recovery path; `networkMode: "always"` as defense-in-depth),
verified live against the rebuilt Docker deployment, no regressions.

\newpage

# 13. What Is Explicitly Not Yet Built

This implementation proves the recommendation, guardrail, and writeback-audit
logic completely, and both UIs completely. It deliberately does **not** yet
include:

- Any AWS Bedrock AgentCore, Strands, or SageMaker code — the orchestration above
  is plain, framework-free Python (a deliberate ADR-0001 choice: the deterministic
  `SupervisorOrchestrator` is portable to a single-file rewrite if a future Strands
  migration happens)
- A real connection to eMRO's Oracle database in production, or a real eMRO
  Writeback REST endpoint — `fake_emro` is a contract-tested stand-in, not a
  simulation to be trusted blindly, but not the genuine integration either
  (see the Integration Handoff Guide for the real contract)
- Real customer data, a signed lighthouse tenant, or any AWS deployment — see the
  **AWS Infrastructure Guide** for exactly what infrastructure-as-code already
  exists toward that and what remains

Per `ROADMAP.md`, this corresponds to substantial completion of Wave 0–2's
application logic (sub-projects #1, #2, #4, #5, #7, #8, #11, plus Wave 4's
sub-project #12 Wave A) in local/reference form, with Wave 3 (go-live, real
tenant onboarding) and the AWS deployment of Waves 0–2's target infrastructure
both not yet started.
