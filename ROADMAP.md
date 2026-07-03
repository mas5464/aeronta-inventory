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
- [x] **Glue loaders for stock_position + current_policy** — PySpark jobs (`stock_position_job.py`, `current_policy_job.py` + shared `glue/_common.py`) following the `demand_history` template; transforms verified on a real local SparkSession (dedup, null-drop, casts, column order); CDK generalized to package all 3 jobs (35 + 11 tests) — 2026-04-17
- [x] **Glue loaders for vendor_economics + part_attributes + criticality** — completes materialization for *every required engine input*. `vendor_economics_job` joins `part_master` costs and synthesizes a `DEFAULT` canonical vendor row (preferred-then-cheapest) alongside per-vendor rows; `part_attributes_job`/`criticality_job` mirror the reco bridge derivations (part_class precedence, essentiality→tier map). Includes cast-fidelity hardening across all jobs (`coerce_int` bround, ANSI-off, string-typed tests). Spark-verified; CDK packages 6 jobs (47 + 11 tests) — 2026-06-26
- [x] **Glue loaders for the derived/graph groups** (`lead_time_distribution`, `open_orders_snapshot`, `interchangeable_graph`, `location_graph`) — **every v1 feature group the engine reads now materializes into Iceberg**. lead_time replicates the bridge's index-based p50/p90/p99 (not `percentile_approx`) + promised-only fallback; open_orders/interchange build sorted `array<struct>`; all mirror the bridge. Spark-verified; CDK packages **10 jobs** (56 + 11 tests) — 2026-06-26
- [x] **Production `GlueIcebergFeatureStore` read client (Phase 6)** — closes extract → Glue → Iceberg → engine. pyiceberg-based (pure Python, no Spark/JVM), catalog injected (GlueCatalog prod / local SqlCatalog tests); per call filters the `tenant_id` partition + key, resolves the latest `extract_date`, maps rows → the same pydantic models as the in-memory stub (incl. demand aggregation, nested array<struct>, flat→nested location node). **Shared contract test** proves in-memory ≡ Iceberg observational equivalence across all 12 methods + identical tenant-isolation errors (ADR-0002 task 24). Adversarial review folded in: wash_rate exploded-aggregation fix, last-write-wins on re-appended partitions, empty-table handling. 87 feature-store tests (27 new), ruff clean — 2026-06-26
- [x] **DynamoDB online-feature layer (design §4.2)** — the low-latency event-triggered read path. `DynamoDbOnlineStore` serves one denormalized `FeatureBundle` per `(tenant_id, pn, location)` (single sub-10ms `get_item` vs ~12 reads); item shape matches the CDK `pn_location` key, bundle stored as JSON, boto3 `Table` injected (real CMK table prod / moto tests). `materialize_bundle` assembles a bundle from any `FeatureStoreClient` (Iceberg or in-memory), pulling DEFAULT + open-order vendors, tolerant of absent groups. Tenant chokepoint on reads; moto-backed tests (round-trip, isolation, offline→online). Adversarial review folded in: injective sort key (eMRO `#`-collision fix), `demand_history` windowing under DynamoDB's 400 KB cap, null-required-field contract. 97 feature-store tests (10 new), ruff clean — 2026-06-26
- [x] **Online-table population logic** — `online_writer.populate_online` materializes + upserts each key, skips incomplete keys (no stock → fail closed, not a misleading null-stock row), meters oversize `put` failures; `GlueIcebergFeatureStore.iter_inference_keys` enumerates the `(pn, location)` universe. moto-tested (101 feature-store tests, ruff clean) — 2026-06-26
- [ ] CDK Lambda/Glue *schedule* + event-lane trigger to invoke `populate_online`; 24-month backfill; time-travel queries; `causal_utilization`/`wash_rate` deferred (unused in v1)
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

### Sub-project #4 — Agent Spine (P0, AI platform) 🏗️
Plans: [v1 deterministic core](docs/superpowers/plans/2026-06-27-agent-spine-v1.md) · [original AgentCore plan](docs/plans/2026-04-14-agent-spine-implementation-plan.md) · [design](docs/superpowers/specs/2026-06-27-agent-spine-v1-design.md) · [ADR-0005](docs/adr/2026-06-27-0005-deterministic-agent-spine-core.md)
- [x] **Deterministic orchestration core** (`services/agent-spine/`, `trax_io_spine`): Supervisor sequences the REAL #2 Feature Store + #11 Recommendation Engine → enforces the autonomy tier #11 only suggests (hard §6.2 verify + `BandAutonomyPolicy`) → routes approvals → writes back (`fake_emro`). Protocol seams (`AutonomyPolicy`/`WritebackTarget`/`FeatureStoreClient`) so Strands/AgentCore/Cedar slot in later. 34 tests, ruff clean; built via subagent-driven TDD (12 tasks, per-task + final-review). `trax-io-spine run` delivered **milestone #8** offline (6 recs → 4 queued, 2 hard-rejected) — 2026-06-27
- [ ] `TenantContext` propagation chokepoint in place (`tenant_scope` contextvar) ✅ — extends to Cedar principal verification in the deployment slice
- [x] **Cedar autonomy policy** backing the `AutonomyPolicy` Protocol: `CedarAutonomyPolicy` + `autonomy_bands.cedar` (design §6.1 tier bands) evaluated in-process by `cedarpy` (no AWS), opt-in via DI; fail-safe (parse/eval error → `CedarPolicyError`, never silent allow); Cedar has no float type so delta crosses as integer basis points. 49 tests (15 new), ruff clean; subagent-driven TDD + opus final review — [design](docs/superpowers/specs/2026-06-27-cedar-authorization-design.md) · [plan](docs/superpowers/plans/2026-06-27-cedar-authorization.md) — 2026-06-27
- [x] **Event lane (hot-parts recompute)** — gives #2's DynamoDB online `FeatureBundle` layer its first consumer: a domain event → resolved `(pn,location)` keys → recompute via the existing Supervisor + #11 engine **against the online bundle** → enforce/route/writeback. A `BundleFeatureStore` adapter runs the whole pipeline unchanged on the online layer; `DirectKeyResolver` handles `stock_moved`/`removal_recorded` (fan-out deferred behind the `KeyResolver` Protocol); in-process, no AWS. 64 tests (15 new), ruff clean; subagent-driven TDD + opus final review — [design](docs/superpowers/specs/2026-06-27-event-lane-design.md) · [plan](docs/superpowers/plans/2026-06-27-event-lane.md) — 2026-06-27
- [x] **End-to-end event ingestion** — a consumer-side `EventIngestor` (`event_lane/ingestor.py`) closes the real-time loop: canonical-event JSONL → **dedup by `event_id`** (contract idempotency) → `canonical_adapter` → `EventLaneHandler` recompute → writeback, classifying each event `PROCESSED`/`NO_OP`/`DUPLICATE`/`INVALID` (malformed → dead-lettered, never raises) into an `IngestReport`. New `trax-io-spine ingest` CLI; integration test feeds #3's own `make_event` oracle through it. One-way dep preserved (ingestion in agent-spine, imports the canonical schema). 62 tests (14 new), ruff clean, live CLI verified; subagent-driven TDD + opus final review (**Approve**) — [design](docs/superpowers/specs/2026-06-27-event-ingestion-end-to-end-design.md) · [plan](docs/superpowers/plans/2026-06-27-event-ingestion-end-to-end.md) · [ADR-0008](docs/adr/2026-06-27-0008-consumer-side-event-ingestor.md) — 2026-06-27
- [ ] Strands Supervisor + specialist subagents deployed on AgentCore Runtime (LLM topology, deferred); AgentCore Memory; the AWS event transport (EventBridge/Kinesis/Step Functions); **HTTP ingestion service** (behind the `EventIngestor` seam); fan-out key resolution; incremental online-bundle update; bounded/persistent dedup store; CDK
- [ ] Tracked follow-ups: `_PolicyLike` Protocol for the CurrentPolicy seam; `RestWritebackClient` `aclose()` + FAILED-path test; CLI `--apply`-without-URL guard; align `BandAutonomyPolicy` defaults to §6.1; Cedar schema validation; `cedar.py` return-line `# noqa` cleanup; `adapters.py` `_check` on the two always-raise methods

### Sub-project #5 — Forecasting & Policy Engine (P0, ML engineering) 🏗️
Plans: [slice A — classical intermittent](docs/superpowers/plans/2026-06-27-forecasting-classical-intermittent.md) · [original sub-plan](docs/plans/2026-04-14-forecasting-policy-plan.md) · [design](docs/superpowers/specs/2026-06-27-forecasting-classical-intermittent-design.md) · [ADR-0006](docs/adr/2026-06-27-0006-statistical-projector-behind-demandprojector.md)
- [x] **Slice A — classical intermittent forecasting** (`services/forecasting/`, `trax_io_forecasting`): a `StatisticalProjector` (a `DemandProjector`) fits statsforecast Croston/SBA/TSB (SBC-selected) for the `intermittent` regime → fitted `COMPOUND_POISSON` `DemandProjection` reusing #11's exact distribution machinery (fitted λ vs the historical average); other regimes delegate to the deterministic projector. MASE backtest (champion vs challenger). One backward-compatible #11 change makes `RecommendationService`'s projector injectable. 19 tests + #11's 142 unchanged, ruff clean; subagent-driven TDD + opus final review — 2026-06-27
- [x] **Policy Engine** producing `(ROP, EOQ, SS, Max)` with full provenance — already shipped as #11's deterministic `mini_engine`
- [x] **Slice B — gradient-boosted forecasting** (`moderate`/`high_volume`): a `GradientBoostedProjector` (a `DemandProjector`) fits a per-key autoregressive **sklearn `HistGradientBoostingRegressor`** (lag + rolling features off the gap-filled demand series) → next-period mean + residual variance → the **NORMAL** `DemandProjection` reusing `HistoricalScheduledProjector`'s shape field-for-field (only the mean's source changes); non-target regimes + cold-start delegate to the deterministic fallback. `gb_next_rate` plugs into the existing MASE backtest. 34 tests, ruff clean; subagent-driven TDD + opus final review (**Approve**) — [design](docs/superpowers/specs/2026-06-27-forecasting-gradient-boosted-design.md) · [plan](docs/superpowers/plans/2026-06-27-forecasting-gradient-boosted.md) · [ADR-0009](docs/adr/2026-06-27-0009-gradient-boosted-projector.md) — 2026-06-28
- [ ] Slice B deferred: **LightGBM backend** (needs system `libomp`; sklearn HGB is the verifiable-now default); **causal covariates** (flight hours/cycles/wash rate — `causal_utilization` stubbed in v1); the **global cross-sectional / federated** model
- [x] **Slice C — empirical-Bayes `ULTRA_RARE` projector** (`eb.py` + `peer_priors.py` + `eb_projector.py`): Gamma-Poisson MoM prior from peer group with L0→L3 coarsening backoff; EB-shrunken `COMPOUND_POISSON` projection; new PNs inherit peer-group rate; `build_eb_projector` batch pre-pass; `eb_rate_fn` MASE hook. 61 tests, ruff clean — [ADR-0013](docs/adr/2026-06-28-0013-empirical-bayes-ultra-rare-projector.md) — 2026-06-28
- [ ] Slice C deferred: **Chronos/Moirai zero-shot challenger** (needs `torch` + foundation-model weights / SageMaker hosting)
- [ ] Slice D: SageMaker hosting + nightly champion/challenger evaluation + 45-day auto-promotion gate
- [ ] Forecasting follow-ups: `select_model` `> 2.0` threshold comment ✅; compound-clump (`clump_p`) estimation

### Sub-project #3 — eMRO Outbound Event Publisher (P1, eMRO team + platform) 🏗️
Plan: [2026-04-14-event-publisher-plan.md](docs/plans/2026-04-14-event-publisher-plan.md) · Slice-A spec/plan: [design](docs/superpowers/specs/2026-06-27-event-publisher-contract-harness-design.md) · [plan](docs/superpowers/plans/2026-06-27-event-publisher-contract-harness.md)
Contract: [2026-04-14-emro-event-publisher-contract.md](docs/contracts/2026-04-14-emro-event-publisher-contract.md) · [ADR-0007](docs/adr/2026-06-27-0007-event-publisher-canonical-contract-harness.md)
- [x] **Slice A — canonical wire-contract + `fake_emro` harness** (`services/event-publisher/`, `trax_io_event_publisher`): full-fidelity 7-event schema (rich envelope, UUIDv7/semver/kebab validators, smart-union, untrusted-field markers) as single source of truth; `EventPublisher` retry/backoff/DLQ behind a `Transport` seam; FastAPI `fake_event_endpoint` (202/400/403/409/429 + idempotency + replay); `AsgiTransport` in-process round-trip; `test_contract_examples.py` parses all 7 contract examples verbatim; **64 tests**. Consumer reconciliation via one-way `event_lane/canonical_adapter.to_domain_event` (slim models unchanged; agent-spine 48 tests incl. canonical→adapter→handler round-trip) — 2026-06-27
- [x] `schema_version` field semver-governed; contract-first (canonical schema + `schema_version_compatible`) — 2026-06-27
- [ ] **Deferred (Java-in-eMRO + AWS):** Oracle triggers / CDC; Spring Boot drainer; real mTLS client + AWS Private CA cert issuance; EventBridge + per-tenant Kinesis streams; S3 audit bucket; operator UI; per-kind feature flags — all behind the `Transport`/`DeadLetterQueue` stubs
- [ ] **Deferred:** Schemathesis property-based tests (pins an older fastapi that breaks the `http` extra on py3.14 — revisit with a py3.14-compatible pin)
- [ ] Ships inside coordinated eMRO release

### Sub-project #6 — eMRO Writeback REST API (P1, eMRO team) 🏗️
Plan: [2026-04-14-writeback-rest-plan.md](docs/plans/2026-04-14-writeback-rest-plan.md) · Hardening slice: [design](docs/superpowers/specs/2026-06-28-writeback-hardening-design.md) · [plan](docs/superpowers/plans/2026-06-28-writeback-hardening.md) · [ADR-0010](docs/adr/2026-06-28-0010-audited-writeback-seam.md)
- [x] **Local hardening slice** (`writeback/`, against `fake_emro`): `AuditedWritebackTarget` Protocol (extends `WritebackTarget` with `get_history` + `rollback`); per-key provenance `HistoryEntry` ledger (monotonic version, parent_version chain, full provenance incl. tier); rollback over a configurable **non-zero 90-day** window (revert + chained entry; first-write → `NOTHING_TO_REVERT`); **shadow-mode** (`WritebackStatus.SHADOWED` + `trax-io-spine run --shadow`) logging every would-be write without applying. `fake_emro` is **backed by** an `InMemoryWritebackTarget` (one behavior definition, no mock drift); `RestWritebackClient` mirrors the surface. 88 tests, ruff clean; subagent-driven TDD + opus final review (**Approve**) — 2026-06-28
- [x] Rollback path exercised (90-day window configurable, non-zero) — `fake_emro` + in-memory
- [x] `fake_emro` contract test suite green (history/shadow/rollback round-trip over httpx ASGI)
- [ ] **Deferred (real eMRO):** REST surface scoped strictly to `PN_INVENTORY_LEVEL` (Oracle/Spring); transactional writes to real `PN_INVENTORY_LEVEL_HISTORY`; mTLS+JWT+principal auth; business-rule validation; rate limiting; bulk-rollback + confirmation-token; persistence (S3/DynamoDB); `stock_level_changed` event emission
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

### Sub-project #7 — Planner UI "Trax IO Review" (P1, eMRO team) 🏗️
Plan: [2026-04-14-planner-ui-plan.md](docs/plans/2026-04-14-planner-ui-plan.md) · BFF slice: [design](docs/superpowers/specs/2026-06-28-planner-ui-bff-design.md) · [plan](docs/superpowers/plans/2026-06-28-planner-ui-bff.md) · [ADR-0011](docs/adr/2026-06-28-0011-planner-ui-bff.md)
- [x] **BFF slice** (`agent-spine/bff/`, `--extra bff`): the Trax-side FastAPI backend-for-frontend. `PlannerStore` (in-memory, per-tenant, seeded from the real Supervisor pipeline, keeping the `(rec, outcome)` pairs the Supervisor discards) + `create_planner_app`. Endpoints: priority-sorted queue, provenance detail, approve/reject/defer, bulk-approve by filter, history, rollback, per-tenant kill switch (engaged ⇒ `423`). Reuses #11 engine + #4 guardrail + #6 audited writeback unchanged; tenant-isolated. **24 BFF + 112 agent-spine tests, ruff clean; live queue→approve→history verified.** Its OpenAPI is the contract for the React frontend. — subagent-driven TDD + opus final review (**Approve**) — 2026-06-28
- [x] Pending-recommendations queue with provenance surfaced — BFF (`GET /recommendations` + `/{id}`)
- [x] One-click approve / reject / defer — BFF (`POST .../approve|reject|defer`)
- [x] Bulk-approve by filter — BFF (`POST /recommendations/bulk-approve`)
- [x] Kill switch (per-tenant) — BFF toggle (engaged ⇒ approvals blocked); the 60-second runtime revert-to-shadow propagation deferred
- [x] **React frontend** (`apps/planner-ui/` — the repo's first frontend; React 18 + TS + Vite 5 + Vitest 2 + CSS Modules): the core approval loop — priority-sorted `QueueTable` (tier badges, criticality, keyboard-operable selection, approve gated by `approvable`), `DetailPanel` (current→proposed diff + why-queued + evidence + approve/reject/defer), `KillSwitchHeader` (engaged ⇒ approve disabled) — over the BFF via a typed `PlannerClient` (`HttpPlannerClient` + `FakePlannerClient`). Built/tested in a scratchpad outside iCloud, **source-only committed** (`node_modules` gitignored). **25 Vitest tests, tsc + vite build clean; live queue→provenance→approve verified.** 3-lens adversarial review (react / a11y / contract) → fixes landed. — [design](docs/superpowers/specs/2026-06-28-planner-ui-react-design.md) · [ADR-0012](docs/adr/2026-06-28-0012-planner-ui-react-frontend.md) — 2026-06-28
- [x] **React follow-ups (guards + bulk-approve + history)** — double-submit + getDetail-race guards in `usePlanner` (`busy` gate + `selectSeq` token, wired to disable action buttons mid-write); `BulkApproveBar` filter builder (tier checkboxes / max change % / min criticality) → `bulkApprove` over the BFF filter endpoint; inline writeback-history timeline + per-entry rollback in `DetailPanel` — faithful to the in-memory target (first agent write has null `old_values` ⇒ `nothing_to_revert`). Typed client extended (`bulkApprove`/`getHistory`/`rollback` + TS mirrors of `HistoryEntry`/`RollbackRequest`/`RollbackResult`); `FakePlannerClient` mirrors the server and now defensively copies its seed. **46 Vitest tests (was 25), tsc + vite build clean; live queue→history→rollback + Tier-A bulk-approve screenshotted.** — 2026-06-28
- [x] **React follow-ups (Pending / Decided tabs)** — a lightweight `Tabs` switch (local state, no router) over the BFF's existing `status` queue filter: the **Decided** tab merges approved/rejected/deferred and shows a per-row status badge (no approve action); selecting a decided row reuses `DetailPanel` in a read-only mode (approve/reject/defer hidden) so its **writeback-history timeline + rollback** are now reachable — closing the "history is queue-bound to pending" gap *and* the tabs follow-up in one slice. `getQueue(tenant, status)` (HTTP `?status=`; fake filters by status); `usePlanner` gained `tab`/`setTab` (switching clears selection). **55 Vitest tests (was 46), tsc + vite build clean; live approve → Decided tab → status badge → writeback history (v2+v1) + rollback verified.** — 2026-06-28
- [x] **React follow-ups (types filter · WCAG · routing)** — bulk-approve gained a recommendation-type multiselect (`BulkApproveFilter.types`); a WCAG pass made the tabs a full WAI-ARIA tabs widget (roving tabindex, Arrow-key nav + roving focus, `aria-controls`, focusable `role="tabpanel"` with `aria-labelledby`, `role="status"` loading region); and the active tab now lives in the URL hash via **react-router-dom `HashRouter`** (`#/pending`, `#/decided` — deep-linkable, browser back/forward), App self-wrapping the router with a thin URL→`usePlanner` sync (v7 future flags on; output pristine). **62 Vitest tests, tsc + vite build clean; live deep-link + tab→hash + type filter verified.** — 2026-06-28
- [x] **Ops-console redesign** — reworked the Pending view into a data-dense operations console (user request, grounded via the `ui-ux-pro-max` Data-Dense Dashboard pattern + an approved `visualize` mockup, inspired by the Servigistics/MTBF/ThingWorx/Inventar references): `NavRail` app shell; a single `Toolbar` (search + tier/type/AOG filters + CSV Export + "Approve matching" — bulk-approve unified, `BulkApproveBar` retired); `SummaryCards` (pending/net cost/AOG risk/Tier-A) + `ChartRow` (by-type donut, by-tier bars); denser sortable `QueueTable` (AOG + confidence columns, `aria-sort`, status badges). Pure `lib/queryView.ts` (search/filter/sort/summary/CSV, 10 tests) + `lucide-react` icons. Frontend-only over existing BFF data; **78 Vitest tests (was 62), tsc + vite build clean; live search/filter/sort/cards/charts verified.** — 2026-06-28
- [x] **Part context + portfolio dashboard** — BFF gained two read-only reporting endpoints (`GET /parts/{pn}/{location}` → `PartContext`; `GET /dashboard` → `DashboardSummary` with portfolio KPIs + by-criticality/ATA/part-class/tier breakdowns + top-shortages), served off `PlannerStore.from_extract`'s retained feature store + keys universe. UI: enriched `QueueTable` with **On hand** / **Need** columns; a **part drawer** in `DetailPanel` (description, stock breakdown, lead time, open orders, inline-SVG `DemandTrend` chart) loaded lazily via `getPartContext`; a new **Dashboard** view at `#/dashboard` with the **NavRail "Dashboard" item now live**. UAT.md extended (sections N/O) with 14 new manual cases mapped to their Vitest tests. **96 Vitest tests (was 78); 136 BFF + agent-spine tests (was 112).** — 2026-07-01
- [x] **React follow-ups (remaining) — closed:** `DetailPanel` is now a right-side overlay `Drawer` (focus trap, Escape/backdrop/×/re-click-toggle to close) deep-linked via a new `/:tab/:id` route synced with `usePlanner`; bulk-approve per-item outcomes (previously fetched then discarded) now surface as an expandable disclosure when results aren't uniform; a new dependency-free WCAG contrast test parses the real `tokens.css` and locks in a tiered AAA(7:1)/AA(4.5:1) policy across 48 color pairs, with the 11 token-value fixes it required. Whole-branch review caught 1 real Critical (acting on a recommendation *from the open drawer* left a stale id in the URL, reopening the drawer on the just-decided item — an interaction between the new routing and pre-existing write-completion logic invisible to any single task review) — fixed + independently re-verified. **184 planner-ui tests (was 111), tsc clean.** — 2026-07-03
- [ ] **Deferred:** weekly Tier-C digest; essentiality mapping + service-level/autonomy-band settings surfaces; SSE real-time; bulk-rollback + confirmation; NL-explanation agent; auth (JWT/Cedar) + DynamoDB persistence; embedded in lighthouse eMRO
- [x] **`apps/web` — second frontend (spec-faithful, all 7 views) — Slice S8 (Hardening, final slice)**: `apps/web` (React 18 + TS + Tailwind + shadcn/ui + TanStack Query, built independently of `apps/planner-ui` over slices S1–S7, dockerized on **:8089** alongside planner-ui's :8088) renders the **full PRD §6 surface** — Overview, Part Drill-Down, Workbench, AI Recommendations, Forecast & Service Levels, What-If Scenarios, Data & Connections — over the same BFF, with the provenance invariant (`Metric`/`ProvChip`/`MetricValue<T>`) enforced end-to-end. **S8 hardening**: a WCAG 2.1 AA a11y pass (dependency-free `useFocusTrap` hook — trap + Escape-close + focus-restore — wired into the reject/commit-confirm dialogs; `scope="col"` on every table; `focus-visible` rings on nav; caught and fixed a real focus-loss bug in `SavedScenarios`'s commit-confirm swap pattern); a shared `<QueryState>` (loading/error-with-Retry/empty) helper consolidating all 7 views' query states + adding a working Retry to every one; `staleTime: 60s` on the read-heavy dashboard/forecast/feeds/part-context queries with the real query `dataUpdatedAt` (not render-time "now") now feeding the `ProvChip` freshness tooltip; documented + bounds-tested the Workbench's pagination-is-the-40k-SKU-strategy (`MAX_PAGE_SIZE = 200`, proven smooth at a full 200-row page, no virtualization library); one best-effort Playwright e2e spec (`npm run e2e` — Workbench accept-removes-row against a route-mocked BFF). **142 Vitest tests (was 112), build + lint clean.** [apps/web/UAT.md](apps/web/UAT.md) created (11 areas, 72 cases, 70 automated). — 2026-07-01
- [x] **Fast-boot feature-store snapshot** — BFF container boot **~7s (was ~190s)**: new `trax_io_feature_store.snapshot` (interned JSON `dump_store`/`load_store`, pydantic validation ON at load, fail-loud `SnapshotFormatError`), `trax-io-precompute --out` now emits a **complete snapshot dir** (`feature_store.json` 55MB vs the 282MB extract + keys/manifest/recs/meta), `PlannerStore.from_snapshot_dir` + `PLANNER_SNAPSHOT_DIR` asgi precedence, compose drops the extract mount (healthcheck `start_period` 240s→30s). Equivalence vs `from_extract` proven (queue/dashboard/part-context/keys/manifest); live-verified on the real 21.2K-key portfolio (12,876 recs, RAM 619MiB). 108 feature-store + 212 agent-spine tests — [spec](docs/superpowers/specs/2026-07-02-fast-boot-feature-store-snapshot-design.md) · [plan](docs/superpowers/plans/2026-07-02-fast-boot-feature-store-snapshot.md) — 2026-07-02
- [x] **Full-network 62K run (Wave-3 W3-6 closed)** — the deployed portfolio is now the **entire planning-active network: 58,899 judgeable keys** (of the 62,492-key universe; 24,241 PNs), 41,740 recs (11,082 pending / 30,658 hard-rejected), **boot 14.3s / RAM 1.5GiB / snapshot 152MB**. Required a real scope fix caught before cutover: network-wide `part_location` domains now carry a planning-active `EXISTS` row filter + the pooled loader skips zero-policy rows (the unfixed path admitted **984,021** keys — 15.7× the true universe). 115 extract + 147 reco tests — 2026-07-02
- [x] **`apps/web` post-S8 feature arc (F1–F5) + drill search** — shared client-side table primitives (`useTableState`/`useUrlSyncedState` + `SortHeader`/`TableChrome`), BFF **server-side sort/filter** on the queue endpoint consumed by a URL-synced Workbench, **in-place drill panels** on every Overview card (by_part_class/by_tier rendered for the first time), and the drill `BreakdownTable`'s **search box on ≥15-row breakdowns** (by-ATA's ~48; displayed-label matching, no-match message) — closing F1's last unused capability. UAT.md gains B7/B8 (drill + search). **231 Vitest tests** — 2026-07-02

### Sub-project #8 — Business Value Report Pipeline (P1, ML engineering) 🏗️
Plan: [2026-04-14-bvr-pipeline-plan.md](docs/plans/2026-04-14-bvr-pipeline-plan.md) · v1-local slice: [spec](docs/superpowers/specs/2026-07-02-bvr-pipeline-v1-local-design.md) · [plan](docs/superpowers/plans/2026-07-02-bvr-pipeline-v1-local.md)
- [x] **v1-local BVR pipeline** (`trax_io_spine.bvr` — models/attribution/report/svg/render/pdf): schema-locked `BvrReport 1.0.0`; **projected-only** savings decomposition (holding/ordering/stockout-risk, disclosed formulas + rates, applied-vs-shadowed split, "N of M valued" coverage) against the genuine pre-agent baseline (extract `CurrentPolicy` + writeback ledger via new `iter_history`); tier service **posture** (not realized) + governance (approval/override/rollback/tier-mix/kill-switch) + forward look; Jinja2 printable HTML with inline-SVG charts (Chart.js/pyppeteer dropped) behind a `bvr` extra; WeasyPrint PDF behind a `pdf` extra (skip-clean tests; macOS `python -m pytest` + DYLD note in lessons). BFF: memoized `PlannerStore.bvr()` (invalidated by every decision) + `GET /reports/bvr{,.html,.pdf}` (501 without pdf extra); planner-ui **Reports** section live at `#/reports`. Review waves caught + fixed 2 real bugs: silent Jinja2 autoescape-off (`.j2` suffix never matched — injection-proven, now `autoescape=True` + hostile-value test) and a 2.6MB/2-min-PDF methodology hash list at 58.9K keys (now 12-sample + count). **242 agent-spine + 110 planner-ui tests.** — 2026-07-02
- [x] Monthly BVR schema locked — `BvrReport` pydantic `schema_version 1.0.0` + field-snapshot tripwire test — 2026-07-02
- [x] Savings-attribution methodology implemented — projected-only vs the pre-agent extract baseline (honest v1-local scope; realized-vs-counterfactual unlocks with sequential monthly extracts, per spec) — 2026-07-02
- [x] WeasyPrint rendering pipeline — HTML (jinja2 + inline SVG) + PDF; Docker image gains pango/cairo — 2026-07-02
- [x] Auto-post to Planner UI — intrinsic: the report serves from the live store at stable `/reports/bvr*` URLs; Reports nav section live — 2026-07-02
- [x] First BVR delivered — generated + served (JSON 5.4KB / HTML / PDF 27KB in 2.2s) over the real **58.9K-key full-network deploy**; live loop proven (approve → savings/governance update). Real lighthouse-*customer* delivery pending Week-0 signing — 2026-07-02
- [x] **Follow-ups closed** (6 of 7 reviewer-triaged items from the final review; `-0.00` Decimal edge stays on its own background task): `DEFERRED_OPEN_ORDER` exclusion now commented + tested; stockout coverage floored at 0 per spec text; ordering-skip count surfaced in `ordering_cost_delta.inputs`; **`Methodology.keys_total_portfolio`** discloses the KeyStats-valued-subset gap (mirrors `ScenarioSolver.total_keys_in_universe`) — `BvrReport` **`schema_version` 1.0.0 → 1.1.0**; BFF report routes gain a `_bvr_or_500` safe-degrade wrapper; spec §1 mean-vs-p50 wording fixed. **250 agent-spine (was 242) + 111 planner-ui (was 110) tests**, ruff/tsc clean; live-verified on the 58.9K deploy (`keys 57,605 / keys_total_portfolio 58,899`) — 2026-07-02

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
- [x] Week 8 — First Agent Spine recommendation produced — ✅ 2026-06-27, via the deterministic core against the REAL #11 engine (not a stub): `trax-io-spine run` → 6 recs, 4 queued / 2 hard-rejected
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
