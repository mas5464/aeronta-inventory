# Roadmap — Trax IO v1

**Source of truth:** [docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md](docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md)
This file mirrors that roadmap's milestones so Claude can track progress inline. If the two diverge, the dated roadmap in `docs/roadmap/` wins — update this file to match.

## Progress Convention
- `[ ]` = Todo
- `[-]` = In Progress 🏗️
- `[x]` = Completed ✅

---

## Planning Phase (pre-build)

- [x] v1 design doc approved ([design](docs/design/2026-04-14-trax-io-inventory-optimizer-design.md)) — 2026-04-14
- [x] v1 master build roadmap approved ([roadmap](docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md)) — 2026-04-14
- [x] ADRs 0001–0003 drafted (Strands vs LangGraph; in-memory feature-store stub; fake-eMRO contract testing) — 2026-04-14
- [x] eMRO Outbound Event Publisher contract drafted ([contract](docs/contracts/2026-04-14-emro-event-publisher-contract.md)) — 2026-04-14
- [x] 10 sub-project plans drafted ([plans](docs/plans/)) — 2026-04-14
- [x] Steering-committee one-pager drafted ([exec](docs/exec/2026-04-14-trax-io-steering-committee-onepager.md)) — 2026-04-14
- [x] Engineering execution, architecture, and integration handoff guides generated — 2026-04-14
- [x] Project scaffold (CLAUDE.md / ROADMAP.md / TASKS.md / .claude/) — 2026-04-16

---

## Wave 0 — Foundation (target: 6 weeks; parallel start)

### Sub-project #1 — Nightly Extract Utility (P0, eMRO team) 🏗️
Plan: [2026-04-14-nightly-extract-utility-plan.md](docs/plans/2026-04-14-nightly-extract-utility-plan.md)
- [x] Phase 1 scaffold: `tools/nightly-extract/` (Python CLI skeleton + 12 SQL placeholder files + uv/pytest/ruff) — 2026-04-16
- [x] Phase 1 → 21-domain real build: canonical SQLs parameterized with Oracle bind vars + `ExtractManifest` contract + atomicity policy (45 tests) — 2026-04-17
- [x] Oracle package dependencies resolved — PL/SQL logic inlined into SQLs (zero dependency on `PKG_TRAX_PTC` / `pkg_settings_pn_master`) — 2026-04-17
- [x] Phase 2 slice — `oracledb` thin driver, per-domain bind-var execution, runner with per-domain atomicity + manifest emission, dry-run mode (73 tests) — 2026-04-17
- [x] Fixed `stock_level_upload` #19 PN/LOCATION alias transposition at the SQL source; added `pythonpath=["src"]` to pytest config — 2026-04-17
- [x] **S3 landing writer** — `LandingSink` abstraction (`LocalFsSink` + boto3 `S3Sink` with SSE-KMS), runner writes through it + populates `s3_uri`, manifest landed last, CLI `--landing s3://… --kms-key-id`, lazy boto3 (81 tests) — 2026-04-17
- [ ] Phase 2/3 finish — real Oracle smoke test against customer staging; Parquet + parallel/retry tuning; presigned-URL `LandingSink` (credential-less, customer-run)
- [ ] Trax-signed extract utility packaged (Oracle PL/SQL + Python CLI)
- [ ] Pre-approved customer security-review package shipped
- [ ] Lighthouse tenant nightly extract runs clean for 14 consecutive days

### Sub-project #2 — Feature Store & Data Lake (P0, Data platform) — **critical path** 🏗️
Plan: [2026-04-14-feature-store-plan.md](docs/plans/2026-04-14-feature-store-plan.md)
- [x] Phase 1 scaffold: `services/feature-store/` + `infra/feature-store/` (Iceberg schema + Glue job skeletons + CDK synth) — 2026-04-16
- [x] Phase 2 slice — PySpark `demand_history` Glue job (manifest-driven, Iceberg writer, `unionByName`/ANSI-safe casts) + CDK Glue job packaging w/ tenant-scoped IAM role (28+11 tests) — 2026-04-17
- [x] Promoted `stock_position` (#18) + `current_policy` (#19) into FS read groups: schemas + `FeatureStoreClient.get_stock_position`/`get_current_policy` + InMemory buckets + Iceberg column maps; engine now reads stock/policy from FS (31 tests) — 2026-04-17
- [x] **Data bridge** — `extract_loader.build_stores_from_extract` turns a real nightly-extract output dir into a judge-able recommendation batch (shadow-mode dry run, no AWS/Oracle/Spark): column-maps + monthly demand bucketing + lead-time/open-orders/interchange derivations; CLI `--extract-dir` + sample extract + golden test — 2026-04-17
- [ ] Iceberg schema + partitioning (`tenant_id`, `extract_date`) finalized across all 21 domains; Glue loaders for stock_position/current_policy/scheduled_demand
- [ ] Glue jobs ingesting nightly extracts for lighthouse tenant
- [ ] 24 months of history backfilled for lighthouse tenant
- [ ] DynamoDB online-feature layer live, sub-10ms reads verified
- [ ] Time-travel queries exercised for audit reproducibility

### Sub-project #9 — Observability + SOC 2 Control Plane (P0, Platform + SecOps) 🏗️
Plan: [2026-04-14-observability-soc2-plan.md](docs/plans/2026-04-14-observability-soc2-plan.md)
- [x] Phase 1 scaffold: `infra/observability-soc2/` (CDK stack for CloudTrail Lake + Audit Manager + KMS + Object-Lock audit bucket + OTel collector, synth only; synth tests + `docs/soc2-onboarding.md`) — 2026-04-16
- [x] Phase 2 slice — multi-tenant refactor: `TenantSpec` dataclass + `LIGHTHOUSE_TENANTS`, per-tenant KMS/log-group loop, `CfnOutput` exports (`TraxIo-<tenant>-TenantKmsArn`, `TenantLogGroupArn`), tenant-tag IAM helpers (40 tests) — 2026-04-17
- [ ] CloudTrail Lake event store live with 7-year retention
- [ ] AWS Audit Manager SOC 2 framework attached
- [ ] Per-tenant KMS keys with annual rotation provisioned
- [ ] OpenTelemetry → X-Ray pipeline emitting trace-per-agent-hop
- [x] SOC 2 engineer onboarding doc published (mandatory reading for all sub-plans) — `infra/observability-soc2/docs/soc2-onboarding.md` — 2026-04-16

**Wave 0 exit:** Extract clean 14 days; Feature Store backfilled; CloudTrail Lake live; Audit Manager attached.

---

## Wave 1 — Core Agent + Write Path (target: 10 weeks; overlaps Wave 0 tail)

### Sub-project #4 — Agent Spine (P0, AI platform)
Plan: [2026-04-14-agent-spine-implementation-plan.md](docs/plans/2026-04-14-agent-spine-implementation-plan.md)
- [ ] Strands Supervisor deployed on AgentCore Runtime
- [ ] 6 specialist subagents scaffolded (Data/Retrieval, Regime Router, Forecasting stub, Policy Engine stub, Guardrail/Approval, Writeback stub)
- [ ] `TenantContext` propagation + `Specialist._assert_tenant_match` in place
- [ ] End-to-end dry-run against lighthouse tenant data with stub forecaster

### Sub-project #5 — Forecasting & Policy Engine (P0, ML engineering)
Plan: [2026-04-14-forecasting-policy-plan.md](docs/plans/2026-04-14-forecasting-policy-plan.md)
- [ ] Regime router rule set implemented (ultra_rare / intermittent / moderate / high_volume)
- [ ] Champion models deployed per regime (Compound-Poisson/EB, Croston/TSB/SBA, LightGBM, ensemble)
- [ ] Challenger shadow-scoring loop live
- [ ] Deterministic Policy Engine producing `(ROP, EOQ, SS, Max)` with full provenance
- [ ] Nightly champion/challenger evaluation pipeline on SageMaker + Glue

### Sub-project #3 — eMRO Outbound Event Publisher (P1, eMRO team + platform)
Plan: [2026-04-14-event-publisher-plan.md](docs/plans/2026-04-14-event-publisher-plan.md)
Contract: [2026-04-14-emro-event-publisher-contract.md](docs/contracts/2026-04-14-emro-event-publisher-contract.md)
- [ ] 7 domain events implemented (`flight_completed`, `stock_moved`, `wo_scheduled`, `vendor_price_changed`, `plan_published`, `removal_recorded`, `eo_published`)
- [ ] mTLS endpoint + EventBridge + per-tenant Kinesis streams live
- [ ] `schema_version` field semver-governed; contract-first
- [ ] Ships inside coordinated eMRO release

### Sub-project #6 — eMRO Writeback REST API (P1, eMRO team)
Plan: [2026-04-14-writeback-rest-plan.md](docs/plans/2026-04-14-writeback-rest-plan.md)
- [ ] REST surface scoped strictly to `PN_INVENTORY_LEVEL` rows
- [ ] Transactional writes log to `PN_INVENTORY_LEVEL_HISTORY` with full provenance
- [ ] Rollback path exercised (90-day window configurable, non-zero)
- [ ] `fake_emro` contract test suite green
- [ ] Deployed in lighthouse customer eMRO instance
- [ ] First shadow-mode write against real eMRO endpoint

### Sub-project #11 — Recommendation Engine (deterministic v1) (P1, AI platform) 🏗️
Added by [ADR-0004](docs/adr/2026-04-17-0004-deterministic-recommendation-layer.md) — roadmap amendment (register 10 → 11).
Spec: [2026-04-17-trax-io-recommendation-engine-design.md](docs/superpowers/specs/2026-04-17-trax-io-recommendation-engine-design.md) · Plan: [2026-04-17-trax-io-recommendation-engine.md](docs/superpowers/plans/2026-04-17-trax-io-recommendation-engine.md)
- [x] Deterministic 5-type recommendation engine (`services/recommendation-engine/`): net-position core + Purchase/Transfer/Reduce/Sell/Adjust-Min-Max recommenders + AOG risk scorer + arbitration + confidence/ranking; read-only; forward-compatible with #4/#5 contracts — **123 tests green** — 2026-04-17
- [x] Eight acceptance scenarios + invariants green (incl. determinism, no-contradiction, contract pins); `click` CLI + optional FastAPI read API — 2026-04-17
- [ ] Promote on-hand-stock / current-policy / scheduled-demand / AOG / repair-TAT stubs into Feature Store #2 (later)
- [ ] Wire group-level apportionment writeback + one-way-directed transfer donors across the full network (currently same-location two-way rollup)

**Wave 1 exit:** Agent produces real recommendations; forecast champion/challenger running nightly; Writeback deployed; first shadow write landed.

---

## Wave 2 — UI + Value Reporting (target: 8 weeks; overlaps Wave 1)

### Sub-project #7 — Planner UI "Trax IO Review" (P1, eMRO team)
Plan: [2026-04-14-planner-ui-plan.md](docs/plans/2026-04-14-planner-ui-plan.md)
- [ ] Pending-recommendations queue with provenance surfaced
- [ ] One-click approve / reject / defer
- [ ] Bulk-approve by filter
- [ ] Weekly Tier-C digest
- [ ] Kill switch (per-tenant, 60-second revert)
- [ ] Essentiality mapping + service-level target config surfaces
- [ ] Embedded in lighthouse eMRO

### Sub-project #8 — Business Value Report Pipeline (P1, ML engineering)
Plan: [2026-04-14-bvr-pipeline-plan.md](docs/plans/2026-04-14-bvr-pipeline-plan.md)
- [ ] Monthly BVR schema locked
- [ ] Savings-attribution methodology implemented (counterfactual baseline vs pre-agent)
- [ ] WeasyPrint rendering pipeline
- [ ] Auto-post to Planner UI
- [ ] First BVR delivered for lighthouse tenant

**Wave 2 exit:** Planner UI live in lighthouse; first BVR auto-generated.

---

## Wave 3 — Go-Live (target: 4 weeks)

### Sub-project #10 — Tenant Onboarding Runbook (P1, Customer success + ML)
Plan: [2026-04-14-tenant-onboarding-runbook.md](docs/plans/2026-04-14-tenant-onboarding-runbook.md)
- [ ] Essentiality mapping automation (onboarding deliverable)
- [ ] Service-level target calibration consulting playbook
- [ ] Autonomy band tuning protocol
- [ ] Kill-switch ownership RACI
- [ ] 90-day shadow-mode telemetry protocol
- [ ] Canary cohort script (single station, tier-4 expendables, Tier B, 30 days)

**Wave 3 exit:** Lighthouse exits shadow; first Tier B/C writes in production; second tenant signed + onboarding queued.

---

## Lighthouse Customer Milestones
(Mirrors design roadmap's milestone table; mark as weeks tick.)

- [ ] Week 0 — Lighthouse customer signed for shadow-mode pilot
- [ ] Week 2 — Customer's nightly extract runs clean against pilot dataset
- [ ] Week 4 — Feature Store backfilled with 24 months of customer history
- [ ] Week 8 — First Agent Spine recommendation produced (stub forecaster)
- [ ] Week 12 — Real Forecasting & Policy stack producing recommendations
- [ ] Week 14 — Planner UI deployed in customer's eMRO
- [ ] Week 16 — Shadow-mode telemetry: agent vs planner decisions
- [ ] Week 20 — First Business Value Report delivered
- [ ] Week 24 — First production Tier B/C writes (non-critical expendables)
- [ ] Week 26 — First SOC 2 Type II attestation complete
- [ ] Week 28 — Second customer signed; v1.1 scope locked

---

## Backlog / Future Phases

### v2 — Causal Demand Forecasting
- [ ] Causal Demand Forecaster specialist
- [ ] Forward flight plan ingestion (OCC / commercial scheduling)
- [ ] Federated peer-benchmark feature lit up as premium SKU

### v3 — AOG & Shortage Risk Agent
- [ ] AOG Risk specialist (Tier A only in v3)
- [ ] Per-tenant AOG cost model calibration (consulting until v3.5)

### v4 — Excess / Obsolete / Redistribution
- [ ] Excess & Redistribution specialist
- [ ] AgentCore Code Interpreter enabled for planner scenario math
- [ ] `CUSTOMER_ORDER_*` demand signal integrated

### v5 — Repair-vs-Buy / Sourcing
- [ ] Sourcing specialist (PO / RO / interchange / rental / loan / pool-exchange / cannibalization)

### v6 — Rotable Pool Sizing (Multi-Echelon METRIC)
- [ ] Rotable Pool specialist
- [ ] Discrete-event simulator (SageMaker + custom container)

### Future ADRs
- [ ] ADR-0004 — Federated cross-tenant feature pipeline isolation model
- [ ] ADR-0005 — AOG cost model calibration methodology
- [ ] ADR-0006 — Multi-echelon (METRIC) simulator architecture (v6)
