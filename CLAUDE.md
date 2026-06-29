# CLAUDE.md — Trax IO (Inventory Optimizer)

## Section A — Project Context

### What this project is
**Trax IO** is a multi-tenant, AI-driven inventory optimization agent layered on the Trax eMRO product for airline spares management. V1 continuously recomputes `(ROP, EOQ, Safety Stock, Max)` per `PN × Location` under tiered autonomy, replacing static values in `PN_INVENTORY_LEVEL` with policy-driven ones.

This repo is currently **planning/documentation-stage**. No application code yet — only design, ADRs, plans, and the roadmap. First build waves start with Wave 0 (Nightly Extract, Feature Store, Observability/SOC 2).

### Owner
Miguel Sosa, VP Head of Innovation, Trax.

### Repo layout
```
airline_inventory_optimizer_plan.md   ← Original seed plan (AWS-flavored)
docs/
  design/      ← Authoritative design doc (Trax-IO v1)
  roadmap/     ← Master build roadmap (10 sub-projects across 4 waves)
  plans/       ← Sub-project implementation plans (one per sub-project)
  adr/         ← Architectural decision records
  contracts/   ← Integration contracts (e.g., eMRO event publisher)
  exec/        ← Executive one-pagers
  guides/      ← .docx handoff guides (architecture, engineering, integration)
  guides-src/  ← Source for guides
  diagrams/    ← Architecture diagrams (src + png)
```

**Authoritative documents (read first before any non-trivial change):**
1. [docs/design/2026-04-14-trax-io-inventory-optimizer-design.md](docs/design/2026-04-14-trax-io-inventory-optimizer-design.md) — locked v1 design
2. [docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md](docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md) — 10 sub-projects, 4 waves, lighthouse milestones
3. Relevant sub-plan under `docs/plans/`
4. Relevant ADR(s) under `docs/adr/`

**External reference artifacts (read-only, outside repo — do not copy in):**
- `~/Downloads/PTC Project Files/eMRO Data SQLs.sql` — **canonical** 21 extract queries against eMRO Oracle. Source of truth for `tools/nightly-extract/sql/`.
- `~/Downloads/PTC Project Files/PKG_TRAX_PTC.sql` — legacy PL/SQL package body (`getKitCost`, `getRecordsType`). Logic is inlined into the extract SQLs so v1 has no package dependency; keep for reference.
- `~/Downloads/PTC Project Files/SPM_Trax_Data_Mapping 1.docx`, `TraxPTCServices 2.docx`, `TraxPTCServices Desgin Flow.docx` — customer-facing spec + field-mapping docs for the legacy PTC integration.
- `~/ptcwebservice/PTCWebService/` — **legacy Java reference implementation** (~477 files, Maven project `trax.aero.*`). Covers both extracts and eMRO writeback (`src/main/java/trax/aero/controller/*DataService*.java`, `inventory/`, `orders/`). Cross-reference when re-implementing #1 Phase 2 (real Oracle execution, bind-var patterns) and #6 Writeback REST (esp. `controller/PnInventoryDataService.java`). Do NOT port Java code directly — v1 rewrites in Python/Strands.

### Target tech stack (per design + plans)
- **Agent platform:** AWS Bedrock AgentCore, AWS Strands (Supervisor), Python specialists
- **Models:** Claude Sonnet 4.6 (Supervisor, Regime Router, Guardrail), Claude Haiku 4.5 (Data/Retrieval)
- **ML stack:** SageMaker, `statsforecast`, LightGBM, Chronos/Moirai challengers
- **Data:** AWS Glue + Apache Iceberg (offline), DynamoDB (online), EventBridge + Kinesis (events), S3 + Object Lock (audit)
- **Ingestion:** Oracle PL/SQL extract utility (nightly), eMRO Outbound Event Publisher (Java add-on), optional AWS DMS CDC
- **Writeback:** eMRO Writeback REST API (Java) + Planner UI module
- **Observability / compliance:** OpenTelemetry → X-Ray, CloudWatch, Managed Grafana; CloudTrail Lake (7-yr), AWS Audit Manager (SOC 2 Type II)
- **IaC:** AWS CDK (Python), per-tenant stacks
- **AWS account:** `TraxAi` (single shared account for v1 — per-tenant isolation enforced via KMS keys, Cedar policies, IAM role boundaries, and per-tenant CDK stacks within this account)

### Run / test / build commands
Runnable code lives in these packages (all `uv` + `pytest` + `ruff`, Python ≥3.12, dev deps in `[project.optional-dependencies] dev`):

| Package | Test | Lint |
|---|---|---|
| `tools/nightly-extract` | `cd tools/nightly-extract && uv run --extra dev pytest` | `uv run --extra dev ruff check` |
| `services/feature-store` | `cd services/feature-store && uv run --extra dev pytest` (add `--extra iceberg` for the `GlueIcebergFeatureStore` + contract tests; `--extra dynamodb` for the online-layer tests) | `uv run --extra dev ruff check .` |
| `services/recommendation-engine` | `cd services/recommendation-engine && uv run --extra dev pytest` (add `--extra api` to run the FastAPI tests) | `uv run --extra dev ruff check .` |
| `services/agent-spine` | `cd services/agent-spine && uv run --extra dev pytest` (add `--extra emro` for the `fake_emro` writeback + integration tests; `--extra cedar` for the `CedarAutonomyPolicy` tests; `--extra bff` for the `trax_io_spine.bff` Planner-UI backend tests) | `uv run --extra dev ruff check .` |
| `services/forecasting` | `cd services/forecasting && uv run --extra dev pytest` — slice A `StatisticalProjector` (statsforecast, intermittent; first `uv sync` compiles numba) + slice B `GradientBoostedProjector` (sklearn HistGradientBoosting, moderate/high) + slice C `EmpiricalBayesProjector` (Gamma-Poisson EB, ultra_rare; pure numpy/scipy) | `uv run --extra dev ruff check .` |
| `services/event-publisher` | `cd services/event-publisher && uv run --extra dev pytest` (add `--extra http` for the `fake_event_endpoint` + `AsgiTransport` + conformance tests) | `uv run --extra dev ruff check .` |
| `infra/feature-store` | `cd infra/feature-store && uv run --extra dev pytest` | — |
| `infra/observability-soc2` | `cd infra/observability-soc2 && uv run --group dev pytest` | — |
| `apps/planner-ui` (React/TS — first frontend) | `cd apps/planner-ui && npm install && npm test` (Vitest, 62 tests); `npm run build` (tsc + vite) | `tsc -b` (no ruff — JS/TS) |

- `services/agent-spine` ships the `trax-io-spine` CLI with two commands: `run` — offline orchestration (extract → #2 → #11 → guardrail → writeback) printing an `OrchestrationResult` summary; and `ingest --extract-dir … --tenant … --events <file.jsonl> [--dry-run]` — replays a JSONL of **canonical events** through the consumer-side `EventIngestor` (dedup by `event_id` → `canonical_adapter` → `EventLaneHandler` recompute → writeback), printing an `IngestReport`. `run` also accepts `--shadow` (onboarding mode: logs every would-be write as `SHADOWED` provenance, applies nothing). Example: `uv run trax-io-spine run --extract-dir ../recommendation-engine/examples/extract_sample --tenant acme --dry-run --shadow`.
- The **Planner-UI BFF** (`services/agent-spine/src/trax_io_spine/bff/`, `--extra bff`) is the Trax-side backend-for-frontend for #7 "Trax IO Review": a `PlannerStore` (in-memory, per-tenant, seeded from the real Supervisor pipeline via `from_extract`) + `create_planner_app(stores)` FastAPI app exposing the priority-sorted approval queue, provenance detail, approve/reject/defer/bulk-approve, writeback history + rollback, and a per-tenant kill switch (engaged ⇒ approvals `423`). Run locally: `uvicorn`-host `create_planner_app(...)`. The emitted OpenAPI is the contract for the React frontend; auth/persistence/SSE/digest/settings deferred ([ADR-0011](docs/adr/2026-06-28-0011-planner-ui-bff.md)).
- The **Planner-UI React frontend** (`apps/planner-ui/` — the repo's first frontend; React 18 + TS + Vite 5 + Vitest 2 + CSS Modules, node-20.17-safe) renders the BFF via a typed `PlannerClient` (`HttpPlannerClient` + `FakePlannerClient`): **Pending / Decided tabs** (the Decided tab merges approved/rejected/deferred via the BFF's `?status=` filter and reuses `DetailPanel` read-only so already-approved rows expose their writeback history + rollback) → queue → provenance detail → approve/reject/defer → **bulk-approve filter bar** → **inline writeback-history timeline + rollback** → kill switch, with double-submit + stale-detail guards in `usePlanner`. `node_modules` is gitignored — run `npm install` before `npm test` (the repo now lives outside iCloud at `~/Projects/…`, so the old scratchpad workaround is retired). `VITE_FAKE=1 npm run dev` runs offline (seeded via `SAMPLE_SEED`/`SAMPLE_HISTORY`); `VITE_BFF_URL=… npm run dev` against a live BFF. Tabs are URL-routed via **react-router-dom `HashRouter`** (`#/pending`, `#/decided` — deep-linkable; `App` self-wraps the router, a thin effect syncs the URL into the `usePlanner` tab) and follow the WAI-ARIA tabs pattern (roving tabindex + arrow keys + `role="tabpanel"`); bulk-approve includes a recommendation-type filter. Scope = core loop + guards/bulk-approve(+types)/history + Pending/Decided tabs + WCAG tabs + URL routing; digest, settings, auth, bulk per-result detail still deferred ([ADR-0012](docs/adr/2026-06-28-0012-planner-ui-react-frontend.md)).
- Writeback (`services/agent-spine/src/trax_io_spine/writeback/`) is hardened behind an `AuditedWritebackTarget` Protocol (extends `WritebackTarget` with `get_history` + `rollback`): per-key provenance `HistoryEntry` ledger, rollback over a configurable non-zero window (default 90d), and shadow-mode — all verifiable against `fake_emro` (which is **backed by** an `InMemoryWritebackTarget` to avoid mock drift). Real-eMRO auth/business-rules/persistence/bulk-rollback/events deferred ([ADR-0010](docs/adr/2026-06-28-0010-audited-writeback-seam.md)). It depends on `trax-io-feature-store`, `trax-io-reco`, and `trax-io-event-publisher` (the #3 canonical event schema, consumed by the `event_lane/canonical_adapter`) via **non-editable** `uv` path sources (after editing any, `uv sync --reinstall-package <dist-name>`).
- `services/recommendation-engine` also ships a CLI: `uv run trax-io-reco run --data-file examples/seed.json`.
- It depends on `services/feature-store` via a **non-editable** `uv` path source (cross-project editable installs don't expose src-layout packages — see `.claude/memory/lessons.md`); after editing the feature store, run `uv sync --reinstall-package trax-io-feature-store`.
- CDK stacks: `cdk synth` / `cdk deploy` (per-tenant). Oracle extract: SQL\*Plus / Data Pump on customer scheduler. eMRO add-ons: Java build via the eMRO release train.

### Critical cross-cutting rules
- **SOC 2 Type II from day one.** Every sub-project has SOC 2 hooks (CloudTrail tagging, KMS envelope encryption, audit-log emission). Retroactive evidence is impossible.
- **Tenant isolation at 4 layers:** contract (`TenantContext`), agent (`Specialist._assert_tenant_match`), data (Feature Store namespace + per-tenant KMS), infra (per-tenant CDK stacks).
- **Writeback is the ONLY agent with eMRO write permission.** Every other specialist is read-only.
- **Hard guardrails never bypassed** — see design §6.2. Single-write delta capped at 100% even in Tier C; shelf-life/hazmat/tool-control clamps; active AOG forces Tier A.
- **Feature Store (#2) is the single most load-bearing item.** If it slips, everything downstream slips.

### Conventions observed in existing docs
- Plan / ADR / contract filenames are dated: `YYYY-MM-DD-<slug>.md`.
- Today is 2026-04-16; authoritative docs are dated 2026-04-14.
- Sub-plan register uses P0/P1/P2 priorities and Wave 0–3 sequencing.
- Markdown tables preferred over prose for registers, risks, SLOs.
- Cite sources with markdown links to exact file paths.

---

## Section B — Behavioral Guidelines

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions).
- If something goes sideways, STOP and re-plan immediately — don't keep pushing.
- Use plan mode for verification steps, not just building.
- Write detailed specs upfront to reduce ambiguity.

### 2. Subagent Strategy
- Use subagents liberally to keep main context clean.
- Offload research, exploration, and parallel analysis to subagents.
- For complex problems, throw more compute at it via subagents.
- One task per subagent for focused execution.

### 3. Self-Improvement Loop
- After ANY correction from the user: update `.claude/memory/lessons.md` with the pattern.
- Write rules for yourself that prevent the same mistake.
- Ruthlessly iterate on these lessons until mistake rate drops.
- Review lessons at session start.

### 4. Verification Before Done
- Never mark a task complete without proving it works.
- Diff behavior between main and your changes when relevant.
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness.

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution."
- Skip this for simple, obvious fixes — don't over-engineer.
- Challenge your own work before presenting it.

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding.
- Point at logs, errors, failing tests — then resolve them.
- Zero context switching required from the user.

### Task Management
1. **Plan First**: Write plan to `tasks/todo.md` (or use TodoWrite) with checkable items.
2. **Verify Plan**: Check in before starting implementation.
3. **Track Progress**: Mark items complete as you go.
4. **Explain Changes**: High-level summary at each step.
5. **Document Results**: Add review section to `tasks/todo.md`.
6. **Capture Lessons**: Update `.claude/memory/lessons.md` after corrections.

### Core Principles
- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.

---

## Section C — Workflow Rules
- Before starting any task, check [ROADMAP.md](ROADMAP.md) and mark it `[-]` 🏗️.
- After completing a task, mark it `[x]` with today's date ✅.
- Update [TASKS.md](TASKS.md) at the end of every session with what's done and what's next.
- After ANY correction from the user: update [.claude/memory/lessons.md](.claude/memory/lessons.md) with the pattern.
- Never ask to confirm status updates — just do them.
- When a sub-project graduates from "planning" to "code lives in this repo," add its run/test commands to Section A and create a nested `CLAUDE.md` inside its folder if conventions diverge.
