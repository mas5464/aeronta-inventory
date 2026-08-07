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

**real-eMRO Java track** (`services/emro-writeback-java/`, slices 1–2): slice 1 [spec](docs/superpowers/specs/2026-07-06-emro-writeback-java-slice1-design.md) · [plan](docs/superpowers/plans/2026-07-06-emro-writeback-java-slice1.md) · [ADR-0015](docs/adr/2026-07-07-0015-emro-writeback-java-service.md); slice 2 [spec](docs/superpowers/specs/2026-07-07-emro-writeback-java-slice2-design.md) · [plan](docs/superpowers/plans/2026-07-07-emro-writeback-java-slice2.md) · [ADR-0016](docs/adr/2026-07-07-0016-emro-writeback-slice2.md)
- [x] Quarkus 3 / Java 21 module scaffold, Oracle + Kafka Dev Services, JWT-secured health check — 2026-07-07
- [x] Framework-free domain core `StockLevelWriter`: row validation (min≤max, principal-not-`TRAX_IFACE`, shelf-life/hazmat clamps ported from the rule table), per-item `REQUIRES_NEW` upsert into `PN_INVENTORY_LEVEL` + audit insert, service-owned `WRITEBACK_LEDGER` row making at-least-once delivery **effectively-once**, bounded (3-attempt) version-conflict retry — 2026-07-07
- [x] `WRITEBACK_LEDGER` Flyway `V1` migration: unique `idempotency_key`, unique `(tenant, pn, location, version)` chain, `PROVENANCE_ID` column (added in place pre-deployment) — 2026-07-07
- [x] Facade 2 — PRD batch REST surface (`api/batch/`, camelCase), item-level results incl. `SKIPPED_DUPLICATE` — 2026-07-07
- [x] Facade 1 — Trax IO #6 seam REST surface (`api/traxio/`, snake_case, wire-conforming to `services/agent-spine`'s `RestWritebackClient`/`fake_emro`) covering apply + `get_history`, incl. the tier-`IntEnum` wire-format discovery (accepts both the int and the name string) — 2026-07-07
- [x] Kafka in / results / DLQ topics wired to the same domain core — 2026-07-07
- [x] Micrometer metrics (per-item + per-batch counters/timers) — 2026-07-07
- [x] Env-gated `oracle19c` schema smoke test (connect-only, `EMRO_SMOKE_*` vars, restore-on-Throwable) — 2026-07-07
- [x] **65 tests green**, subagent-driven TDD + per-task adversarial review, head commit `73a9cfd` — 2026-07-07
**Slice 2** (stacked on slice 1, `ab98590`): [spec](docs/superpowers/specs/2026-07-07-emro-writeback-java-slice2-design.md) · [plan](docs/superpowers/plans/2026-07-07-emro-writeback-java-slice2.md) · [ADR-0016](docs/adr/2026-07-07-0016-emro-writeback-slice2.md)
- [x] `WRITEBACK_LEDGER` schema extension (D10): `V1` amended in place (still pre-deployment) — `DOMAIN` (`STOCK_LEVEL`/`REQUISITION`/`TRANSFER`, NOT NULL) + `CREATED_REF` columns added; `MESSAGE` now populated with the per-row outcome message (closes the dead-column carry-forward) — 2026-07-07
- [x] Rollback (D12, contract-conforming): reverts the latest `WRITTEN` entry with non-null `old_values` as a **new write** (new version, `parent_version` → reverted version, provenance `rollback:{provenance_id}`); `NOTHING_TO_REVERT` / `OUTSIDE_WINDOW` (default 90d, configurable, never zero) — matching `fake_emro`/`RestWritebackClient` test-for-test — 2026-07-07
- [x] Out-of-band history (D13): separate `GET /traxio/v1/history/out-of-band` reads `PN_INVENTORY_LEVEL_AUDIT` rows whose `MODIFIED_BY` isn't one of this service's principals (own DTO, no fabricated `version`); the contract `/history` endpoint stays byte-compatible — 2026-07-07
- [x] Requisitions domain (D11): `RequisitionHeader`/`RequisitionDetail` (+ audit) entities lifted from ARMAC (byte-for-byte fidelity); `RequisitionCreator` — validated, ledgered, effectively-once, real-schema `AUTHORIZATION`/`AUTHORIZED_BY`/`TRAX_IFACE` triplet + `STOCK_UOM`-with-`EA`-fallback; batch + Trax IO facades sharing the extracted `WireSanitizer` — 2026-07-07
- [x] Transfers domain: `OrderHeader`/`OrderDetail` (+ audit) entities lifted from ARMAC; `TransferCreator` — type-fidelity restored per the T5 precedent (`orderNumber` stays `long`, `batch` stays `BigDecimal`); batch + Trax IO facades — 2026-07-07
- [x] Kafka domain discriminator (D14): one topic/consumer, an optional `domain` field (`stock_level` default | `requisition` | `transfer`) routes the batch message to the matching processor — backward compatible with slice-1 payloads — 2026-07-07
- [x] Exception taxonomy (D15): new `InfrastructureException` for connection-class failures makes the Kafka infra-retry path **reachable** (was folded into per-row `ERROR`) — propagates → 3-attempt retry → DLQ; REST path unchanged (still per-row `ERROR`, slice-1 wire contract preserved); results/DLQ emitters gain `@OnOverflow` `BUFFER` hardening — 2026-07-07
- [x] Audit-PK collision mitigation (D17): a real-Oracle `ORA-00001` matching `PN_INVENTORY_LEVEL_AUDIT` in the exception text now gets a bounded 2-attempt, ≥1.1s-backoff self-healing retry (stock-level writer only) instead of an immediate `500` — 2026-07-07
- [x] Replay (D16, thin): `GET /api/v1/runs/{runId}/results` re-emits recorded per-row results from the ledger (`writeback:read`), tenant-scoped, ordered oldest-first by `createdAt` then `rowId` (nulls-last for batch-origin rows) — full payload re-drive stays a documented Kafka-retention/offset-reset operation, not this endpoint's job — 2026-07-07
- [x] **143+ tests green** (144 incl. this bookkeeping task's ordering-pin test), head commit `ffc938d` — 2026-07-07
- [ ] **Deferred (production/hardening):** production IdP + broker + deployment; a live smoke-test run against `oracle19c` (env vars documented, not yet executed — now also covers the requisition/order table + `PKG_APPLICATION_FUNCTION` existence checks added by this task, plus D17/LEVEL_ROW_RACE's `ORA-00001` message-text assumption, which needs a production log sample to confirm, not just a read-only probe); eMRO `NotePad`/remarks fields and physical stock-move side effects (documented out of scope, matching `RequisitionCreator`/`TransferCreator`'s trim discipline); a PRD Phase-4 load test against NFR numbers (needs prod-like infra)

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
- [x] **BFF slice** (`agent-spine/bff/`, `--extra bff`): the Trax-side FastAPI backend-for-frontend. `PlannerStore` (in-memory, per-tenant, seeded from the real Supervisor pipeline, keeping the `(rec, outcome)` pairs the Supervisor discards) + `create_planner_app`. Endpoints: priority-sorted queue, provenance detail, approve/reject/defer, bulk-approve by filter, history, rollback, per-tenant kill switch (engaged ⇒ `423`); two read-only reporting endpoints (part context, portfolio dashboard); CSV export; BVR reports (`bvr`/`bvr.html`/`bvr.pdf`). Reuses #11 engine + #4 guardrail + #6 audited writeback unchanged; tenant-isolated. Its OpenAPI is the contract for the frontend. — subagent-driven TDD + opus final review (**Approve**) — 2026-06-28
- [x] Pending-recommendations queue with provenance surfaced — BFF (`GET /recommendations` + `/{id}`)
- [x] One-click approve / reject / defer — BFF (`POST .../approve|reject|defer`)
- [x] Bulk-approve by filter — BFF (`POST /recommendations/bulk-approve`)
- [x] Kill switch (per-tenant) — BFF toggle (engaged ⇒ approvals blocked); the 60-second runtime revert-to-shadow propagation deferred
- [x] **`apps/web` — the sole frontend (spec-faithful, all 7 views + full feature-parity surface)**: React 18 + TS + Tailwind + shadcn/ui + TanStack Query, dockerized on **:8089**, renders the **full PRD §6 surface** over the BFF — Overview, Part Drill-Down, Workbench (server-paged core approval loop), AI Recommendations, Forecast & Service Levels, What-If Scenarios, and Data & Connections — with the provenance invariant (`Metric`/`ProvChip`/`MetricValue<T>`) enforced end-to-end. Built across a hardening slice (S8: WCAG 2.1 AA a11y, shared `<QueryState>` loading/error/empty helper, `staleTime` tuning, documented pagination-not-virtualization strategy) and a post-hardening feature arc (F1–F5: shared table primitives, BFF server-side sort/filter, in-place Overview drill panels + breakdown search), then a 4-wave feature-parity effort that folded in every capability the earlier, now-retired second frontend had (CSV export of the full filtered set; writeback-history timeline + rollback; a Reports view for the #8 Business Value Report; a user-toggleable dark/light theme) so a single app now carries the complete surface. **288 Vitest tests**, build + lint clean; one best-effort Playwright e2e spec. [apps/web/UAT.md](apps/web/UAT.md) is the living regression gate (run before every release). — 2026-07-06
- [ ] **Deferred:** weekly Tier-C digest; essentiality mapping + service-level/autonomy-band settings surfaces; SSE real-time; bulk-rollback + confirmation; NL-explanation agent; auth (JWT/Cedar) + DynamoDB persistence; embedded in lighthouse eMRO
- [x] **Fast-boot feature-store snapshot** — BFF container boot **~7s (was ~190s)**: new `trax_io_feature_store.snapshot` (interned JSON `dump_store`/`load_store`, pydantic validation ON at load, fail-loud `SnapshotFormatError`), `trax-io-precompute --out` now emits a **complete snapshot dir** (`feature_store.json` 55MB vs the 282MB extract + keys/manifest/recs/meta), `PlannerStore.from_snapshot_dir` + `PLANNER_SNAPSHOT_DIR` asgi precedence, compose drops the extract mount (healthcheck `start_period` 240s→30s). Equivalence vs `from_extract` proven (queue/dashboard/part-context/keys/manifest); live-verified on the real 21.2K-key portfolio (12,876 recs, RAM 619MiB). 108 feature-store + 212 agent-spine tests — [spec](docs/superpowers/specs/2026-07-02-fast-boot-feature-store-snapshot-design.md) · [plan](docs/superpowers/plans/2026-07-02-fast-boot-feature-store-snapshot.md) — 2026-07-02
- [x] **Full-network 62K run (Wave-3 W3-6 closed)** — the deployed portfolio is now the **entire planning-active network: 58,899 judgeable keys** (of the 62,492-key universe; 24,241 PNs), 41,740 recs (11,082 pending / 30,658 hard-rejected), **boot 14.3s / RAM 1.5GiB / snapshot 152MB**. Required a real scope fix caught before cutover: network-wide `part_location` domains now carry a planning-active `EXISTS` row filter + the pooled loader skips zero-policy rows (the unfixed path admitted **984,021** keys — 15.7× the true universe). 115 extract + 147 reco tests — 2026-07-02
- [x] **Retired `apps/planner-ui`** — the original React frontend built directly over this sub-project's BFF — once `apps/web` reached full feature parity across all 4 waves (CSV export, writeback history + rollback, Reports/BVR view, dark/light theme); one frontend now carries the complete surface. App + its Dockerfile/compose service deleted; UI-slice specs/plans removed; retirement recorded in [ADR-0014](docs/adr/2026-07-06-0014-retire-planner-ui-frontend.md), which supersedes [ADR-0012](docs/adr/2026-06-28-0012-planner-ui-react-frontend.md). The BFF and all its endpoints are unaffected and keep their "Planner-UI BFF" identity. — 2026-07-06

### Sub-project #8 — Business Value Report Pipeline (P1, ML engineering) 🏗️
Plan: [2026-04-14-bvr-pipeline-plan.md](docs/plans/2026-04-14-bvr-pipeline-plan.md) · v1-local slice: [spec](docs/superpowers/specs/2026-07-02-bvr-pipeline-v1-local-design.md) · [plan](docs/superpowers/plans/2026-07-02-bvr-pipeline-v1-local.md)
- [x] **v1-local BVR pipeline** (`trax_io_spine.bvr` — models/attribution/report/svg/render/pdf): schema-locked `BvrReport 1.0.0`; **projected-only** savings decomposition (holding/ordering/stockout-risk, disclosed formulas + rates, applied-vs-shadowed split, "N of M valued" coverage) against the genuine pre-agent baseline (extract `CurrentPolicy` + writeback ledger via new `iter_history`); tier service **posture** (not realized) + governance (approval/override/rollback/tier-mix/kill-switch) + forward look; Jinja2 printable HTML with inline-SVG charts (Chart.js/pyppeteer dropped) behind a `bvr` extra; WeasyPrint PDF behind a `pdf` extra (skip-clean tests; macOS `python -m pytest` + DYLD note in lessons). BFF: memoized `PlannerStore.bvr()` (invalidated by every decision) + `GET /reports/bvr{,.html,.pdf}`; frontend **Reports** view (now `apps/web`, `/reports`). Review waves caught + fixed 2 real bugs: silent Jinja2 autoescape-off (`.j2` suffix never matched — injection-proven, now `autoescape=True` + hostile-value test) and a 2.6MB/2-min-PDF methodology hash list at 58.9K keys (now 12-sample + count). **242 agent-spine tests.** — 2026-07-02
- [x] Monthly BVR schema locked — `BvrReport` pydantic `schema_version 1.0.0` + field-snapshot tripwire test — 2026-07-02
- [x] Savings-attribution methodology implemented — projected-only vs the pre-agent extract baseline (honest v1-local scope; realized-vs-counterfactual unlocks with sequential monthly extracts, per spec) — 2026-07-02
- [x] WeasyPrint rendering pipeline — HTML (jinja2 + inline SVG) + PDF; Docker image gains pango/cairo — 2026-07-02
- [x] Auto-post to Planner UI — intrinsic: the report serves from the live store at stable `/reports/bvr*` URLs; Reports nav section live — 2026-07-02
- [x] First BVR delivered — generated + served (JSON 5.4KB / HTML / PDF 27KB in 2.2s) over the real **58.9K-key full-network deploy**; live loop proven (approve → savings/governance update). Real lighthouse-*customer* delivery pending Week-0 signing — 2026-07-02
- [x] **Follow-ups closed** (6 of 7 reviewer-triaged items from the final review; `-0.00` Decimal edge stays on its own background task): `DEFERRED_OPEN_ORDER` exclusion now commented + tested; stockout coverage floored at 0 per spec text; ordering-skip count surfaced in `ordering_cost_delta.inputs`; **`Methodology.keys_total_portfolio`** discloses the KeyStats-valued-subset gap (mirrors `ScenarioSolver.total_keys_in_universe`) — `BvrReport` **`schema_version` 1.0.0 → 1.1.0**; BFF report routes gain a `_bvr_or_500` safe-degrade wrapper; spec §1 mean-vs-p50 wording fixed. **250 agent-spine tests (was 242)**, ruff/tsc clean; live-verified on the 58.9K deploy (`keys 57,605 / keys_total_portfolio 58,899`) — 2026-07-02

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

## Wave 4 — Fulfillment-Path Decision Agent

### Sub-project #12 — Fulfillment-Path Decision Agent (P2, eMRO team) 🏗️
Plan: [2026-07-05-fulfillment-decision-agent-wave-a-design.md](docs/superpowers/specs/2026-07-05-fulfillment-decision-agent-wave-a-design.md)
- [x] **Wave A — Requisition data wiring** (`RequisitionSnapshot` schema, domain #9 → feature-store → recommender context): introduced a new `RequisitionSnapshot` schema (deliberately separate from `OpenOrdersSnapshot`, holding `requisition_id` / `qty_needed` / `need_by` / `alt_source_location` from domain #9) + `extract_loader.py` wiring for `order_plan_data_requisition` domain. Exposed via `FeatureReader.get_requisition()` + `ContextAssembler`, flowing into `PartLocationContext.requisition` (optional, matching the `open_orders` pattern). **Explicitly documents a standing v1 limitation:** REPAIR routing will only ever be possible for already-open repair orders (visible via existing `OpenOrdersSnapshot` RO entries), since **no repair-TAT data source exists anywhere in the extract registry** — proposing brand-new repairs as a fulfillment path is out of scope. **113 feature-store + 147 recommendation-engine tests**, ruff clean — 2026-07-05

### Sub-project #13 — Repair-Aware Portfolio Inventory Optimization (P1–P3, advisory) ✅
Plan: [repair-aware-portfolio-inventory-optimization.md](plans/repair-aware-portfolio-inventory-optimization.md)
- [x] **Phase 1 — Time-Correct Key Economics**
- [x] **Phase 2 — Versioned Per-Key Candidate Frontier**
- [x] **Phase 3 — Purchase vs. Repair Lane Separation**
- [x] **Phase 4 — Repair-History Intake and Coverage**
- [x] **Phase 5 — Open-Repair Identity and Conservative Supply**
- [x] **Phase 6 — Age-Conditioned Returns and Independent Scenarios** — 2026-07-28
- [x] **Phase 7 — First Hard-Budget Portfolio Solve**
- [x] **Phase 8 — Tenant-Weighted Deterministic Optimizer**
- [x] **Phase 9 — Asynchronous Full-Portfolio Run Lifecycle**
- [x] **Phase 10 — Explain, Reconcile, and Rerun**
- [x] **Phase 11 — No-Lookahead Replay and Shadow Governance**
- [x] **Phase 12 — Production Contract and Full-Network Launch Gate** — engineering complete; advisory feature flag remains default-off pending pilot coverage review

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

## Commercial SaaS Track (C1–C4)

Spec: [2026-07-20-commercialization-architecture-design.md](docs/superpowers/specs/2026-07-20-commercialization-architecture-design.md) — standalone multi-tenant SaaS ("wrap and persist" the existing v1 backend) on Vercel (marketing + `apps/web` static) + Railway (FastAPI BFF + engine worker) + Supabase (auth/Postgres/storage) + Stripe. Independent of the v1 lighthouse-customer track above; each sub-project gets its own plan.

### C1 — Supabase Foundation
Plan: [2026-07-20-c1-supabase-foundation.md](docs/superpowers/plans/2026-07-20-c1-supabase-foundation.md)
- [x] **Supabase schema + RLS** (`supabase/migrations/`, 4 migrations): tenants/memberships + `current_tenant_id()`; claims hook (`custom_access_token_hook` + defensive `try_uuid`); planner lifecycle tables (recommendations/decisions/writeback_ledger/kill_switches — decisions+ledger append-only at both grant and policy layers); seeded-view/scenario tables (part_keys/part_contexts/tenant_snapshots/scenarios/scenario_audit/bvr_cache) — every tenant-scoped table RLS'd + two-tenant isolation tested. New `services/agent-spine/src/trax_io_spine/pg/` package (`--extra pg`, psycopg3 + testcontainers): pool/tenant-scoped transactions/migration runner, `PgWritebackTarget` (in-memory-conformant ledger), snapshot→Postgres seeder (`trax-io-pg-seed` CLI), `PgPlannerStore` (full `PlannerStore` surface — queue reads, decisions, seeded-views, scenarios, BVR). `bff/asgi.py` gains `DATABASE_URL` boot mode (highest precedence over `PLANNER_SNAPSHOT_DIR`). Subagent-driven TDD, 14 tasks, per-task adversarial review, 6 fix rounds. Pg suite 70 passed / 1 skipped; whole agent-spine suite 365 passed / 2 skipped; ruff clean apart from 2 pre-existing B905 in `tests/bff/test_csv_export.py` — 2026-07-20
- [ ] **Carry-forwards:** 59K scale gate implemented but not yet run (needs a regenerated full-network snapshot — `PG_BENCH_SNAPSHOT_DIR=… pytest tests/pg/test_pg_scale.py`); `DATABASE_URL` boot requires a bypassrls-capable role until a security-definer slug-resolve lands (C2); tenancy RLS policies target `trax_app` only — `authenticated`-role grants arrive with C2
- [x] **C2 pre-flight checklist** — closed by C2 (2026-07-21): idempotency-key expression index on `writeback_ledger` ✅; `bvr()` single-transaction cache atomicity ✅; pg/pg-test extra split ✅; `public.custom_access_token_hook(jsonb)` grant to `supabase_auth_admin` ✅ (migration 0007, hook ACTIVE on real Supabase); one-migration-runner-per-database rule — documented convention in `supabase/README.md`, no code change needed. **Still pending:** run the 59K scale gate when a full-network snapshot is regenerated (carried to C3+).

### C2 — Cloud Deploy
Design: [2026-07-21-c2-cloud-deploy-design.md](docs/superpowers/specs/2026-07-21-c2-cloud-deploy-design.md) (parent spec: [2026-07-20-commercialization-architecture-design.md](docs/superpowers/specs/2026-07-20-commercialization-architecture-design.md))
- [x] **BFF + worker on Railway, `apps/web` on Vercel with auth shell + login** — ✅ 2026-07-21. 12 tasks, subagent-driven with per-task adversarial review, 7 fix rounds (commits `c0adf6d..14c94ed`). **LIVE: https://aeronta-inventory.vercel.app** (apps/web production; `/v1/*` rewrite → Railway BFF, same-origin). Railway project `aeronta`: `bff` (https://bff-production-6568.up.railway.app, `/healthz`-checked, JWT auth ON) + `worker` (jobs poll heartbeating). Supabase `aeronta-inventory`: migrations 0001–0007 applied; Custom Access Token hook ACTIVE (real sign-ins mint tenant claims — verified); smoke user `smoke@aeronta.test` (owner on `aeronta-demo`). End-to-end smoke GREEN through the Vercel rewrite (root 200, unauth 401, authed queue 200, members 200-as-owner) — `deploy/aeronta_smoke.py` is the repeatable, env-gated gate. Tests: pg suite 99 passed/1 skipped; whole agent-spine 365+/2; apps/web 324 Vitest; ruff clean apart from 2 pre-existing B905.
- [ ] **Carry-forwards → C3+:** DB-layer owner-specific membership rules; `emails_for` silent swallow; friendly 403/502 mutation errors + backend email validation; jobs handlers registration (payload must carry `tenant_id` — worker discards it today); 59K scale gate still pending a full-network snapshot; `mailer_autoconfirm` remains false (C4 signup revisits).
- Next: **C4 — Billing + Marketing Site.**

### C3 — Upload Intake ✅ (shipped 2026-07-21, live)
- [x] **Self-serve CSV/xlsx → canonical model v1 → ingest job → recommendations**, live end-to-end on the deployed stack (Vercel + Railway + Supabase). Design spec: [docs/superpowers/specs/2026-07-21-c3-upload-intake-design.md](docs/superpowers/specs/2026-07-21-c3-upload-intake-design.md); plan: [docs/superpowers/plans/2026-07-21-c3-upload-intake.md](docs/superpowers/plans/2026-07-21-c3-upload-intake.md). 10 tasks (0a/0b hardening + 1–8) executed subagent-driven with per-task adversarial review.
  - Canonical model (`trax_io_reco.ingest`: `canonical`/`validate`/`parse`/`mapper`) — 6 files (parts, stock, demand_history, locations, open_orders, vendors), parts+stock required; CSV + xlsx (openpyxl); fail-closed validation → structured `IngestError`s; full-replace seed per tenant.
  - Async direct-to-Storage flow: BFF mints signed Supabase Storage upload URLs (`{tenant_uuid}/{batch_id}/{name}`, cross-tenant/path-traversal-guarded), client PUTs straight to Storage, ingest job created with **minted** paths, worker downloads (service key) → validate → map → seed under a per-tenant `pg_advisory_xact_lock`. Routes: `POST …/uploads`, `POST …/ingest`, `GET …/ingest/{job_id}`; `jobs.result` jsonb; migration 0008 owner-membership RLS + 0009 `jobs.result`.
  - `apps/web` upload panel + ingest history on Data & Connections (role-gated), live in the Vercel bundle.
  - **Live ingest smoke GREEN** end-to-end (`deploy/aeronta_smoke.py` `AERONTA_SMOKE_INGEST=1`): mint → PUT → ingest → poll → `job done · keys=3`.
  - **Deploy-config fix (Task 7):** Railway was falling back to railpack (couldn't build the monorepo) because `deploy/railway-*.json` use non-standard names Railway never auto-reads. Forced the Dockerfile builder per-service via `RAILWAY_DOCKERFILE_PATH` (`bff`→`deploy/bff.Dockerfile`, `worker`→new `deploy/worker.Dockerfile`), pinned `PORT=8000` on `bff`, set `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` on `worker`. See [.claude/memory/lessons.md](.claude/memory/lessons.md).
  - Note: the smoke replaced `aeronta-demo`'s seeded data with the 3-key sample batch — re-seed richer demo data via `trax-io-pg-seed` if wanted.
  - **Final whole-branch review (fable/opus): READY TO MERGE (yes-with-nits).** Tenant isolation (Storage-path guard + service-key download), fail-closed validation, and same-tenant ingest concurrency (advisory lock) all verified clean. Fixed pre-merge: xlsx now content-sniffed by ZIP magic so the per-file dropzones actually work (was extension-detected on extension-less minted paths → silently CSV-parsed); inf/nan rejected in numeric validation; worker `trax_seed`/`WORKER_DATABASE_URL` role documented; stale `deploy/railway-*.json` annotated "not read by Railway". **Carry-forward → C4:** the ingest handler's terminal `jobs.status='done'` write is a separate transaction from the seed commit (the codebase's accepted no-2PC pattern), so a worker crash in that narrow window + a 30-min stale-reclaim could re-run an old batch over newer data (single-tenant, no isolation breach) — fold the terminal write into the seed txn or add a per-tenant seed watermark; also add the T4 forced-overlap concurrency regression guard.

### C4 — Billing + Marketing Site
- [x] **CODE COMPLETE 2026-07-23 (17 tasks, subagent-driven, per-task adversarial review; live rollout pending — runbook: [deploy/C4_ROLLOUT.md](deploy/C4_ROLLOUT.md)).** Design spec: [2026-07-23-c4-billing-marketing-design.md](docs/superpowers/specs/2026-07-23-c4-billing-marketing-design.md); plan: [2026-07-23-c4-billing-marketing.md](docs/superpowers/plans/2026-07-23-c4-billing-marketing.md).
  - **Billing spine:** migrations 0010–0012 (tenants billing columns + `subscription_status` enum, DB-authoritative `plan_tiers` quota map, Stripe mirror `products`/`prices`/`subscriptions` + `stripe_events` idempotency ledger, `leads`, `create_tenant_for_current_user` RPC with bounded slug-retry; RLS on everything, mirror writes service-role-only). **Supabase Edge Functions (Deno, first in repo):** `create-checkout-session` (owner-gated, 14-day card-required trial, `subscription_data.metadata.tenant_id`), `create-portal-link`, `stripe-webhook` (signature-verified, insert-then-apply idempotency with poison-message rollback, tenant fallback via `stripe_customer_id`, `*.deleted`→`active:false`); `config.toml` pins `verify_jwt` per function.
  - **BFF:** `AuthMiddleware` 402 write-gate (lapse→read-only, reads NEVER gated, `past_due` writable, fail-closed 503 on status-read failure, threadpool-offloaded, tenant_conn-based reads — an RLS bug that would have 402'd all prod writes was caught in-review) + `GET /v1/tenants/{t}/billing` (status+usage).
  - **apps/web:** `/signup` wizard (account → email-confirm → org RPC + session refresh → monthly/annual checkout), `/billing` page (usage meter, Portal/Checkout, owner-gated nav), subscription banners, over-quota upgrade CTA (structured-shape matched).
  - **apps/site (Astro 5, new):** marketing home/product/pricing/security + docs (=the connector spec, column-for-column faithful to `canonical.py`) + contact/book-a-demo → `leads`; shared Tailwind preset + tokens.css consumed by both apps; pricing renders from the mirrored Stripe prices at build time (price-agnostic — no dollar amount in code).
  - **Verification (post-final-review fix wave):** pg 150/1skip · bff 180 · web vitest 364 · site vitest 5 · deno 29 · e2e 4 specs; ruff/eslint/deno lint clean.
  - Live cutover checklist (Stripe account/products, secrets, `db push` 0010–0012, function + site deploys, webhook registration, live smoke `AERONTA_SMOKE_BILLING=1`) = [deploy/C4_ROLLOUT.md](deploy/C4_ROLLOUT.md).
  - **Final whole-branch review: fix wave applied (6 code fixes + 4 runbook/docs fixes) — READY TO MERGE.** Fixed a money-path bug (`create_tenant_for_current_user` now upserts `tenant_preferences` on org create, so a stale preference can no longer bind checkout to the wrong tenant after signup), the Edge claims helper's base64url decode (was bare `atob`, 401'd on payloads with `-`/`_`), honest trial/contact copy, and a corrected `stripe-webhook` comment; the runbook now grandfathers the live `aeronta-demo` tenant's `subscription_status` before the 402-gated BFF redeploy and switches `apps/site`/`apps/web` to prebuilt Vercel deploys (`vercel build --prod && vercel deploy --prebuilt --prod` — plain `vercel deploy --prod` now fails remote-side on the shared `packages/tailwind-preset` import). **Carry-forward → C5 — CLOSED 2026-07-25** (a dynamic `TenantRegistry` + `GET /v1/auth/whoami` now serve any tenant with zero manual activation; see the C5 entry below): new-tenant activation no longer stays manual. **Still open, not picked up by C5:** run the Supabase security-advisor check after any `db push` (re default-privilege grants), improve `callFunction`'s error-body UX (it surfaces only the HTTP status, not the Edge Function's JSON error body), and add a token-sync match-assert test (assert `setAccessToken`/`setActiveTenant` actually reflect the latest session rather than just that they were called).

### C5 — Multi-Tenant Serving + Scheduled Recompute
- [x] **CODE COMPLETE 2026-07-25 (13 tasks, subagent-driven, per-task adversarial review; live rollout pending — runbook: [deploy/C5_ROLLOUT.md](deploy/C5_ROLLOUT.md)).** Design spec: [2026-07-24-c5-multi-tenant-serving-design.md](docs/superpowers/specs/2026-07-24-c5-multi-tenant-serving-design.md); plan: [2026-07-24-c5-multi-tenant-serving.md](docs/superpowers/plans/2026-07-24-c5-multi-tenant-serving.md). Closes the C4 carry-forward above: a paying self-serve signup can now reach the product with **zero manual tenant activation**.
  - **Subsystem A — dynamic tenant serving:** migration 0013 (`tenants_for_current_user()`, caller-scoped via `auth.jwt()->>'sub'`); a `TenantRegistry` (`bff/tenant_registry.py`) resolves slug↔uuid against Postgres on demand and lazily caches per-tenant stores, replacing the single-tenant `PLANNER_TENANT` boot requirement (now an optional pre-warm hint only); `AuthMiddleware` resolves through that **same** registry object so the tenant-match assertion can never diverge from the store layer (an unresolvable slug is 403, never a fallthrough); `GET /v1/auth/whoami` replaces the build-time `VITE_TENANT_SLUGS` map — `apps/web` now resolves its tenant at runtime; empty-tenant hardening across all 7 tenant-scoped surfaces (a brand-new tenant with no data gets clean 200s, not crashes).
  - **Subsystem B — scheduled recompute:** migration 0014 (`enqueue_due_recomputes()`, SECURITY DEFINER, not exposed over HTTP) backs a **pg_cron** nightly job (`0 3 * * *`) that enqueues `jobs(kind='recompute')` rows for eligible tenants (prior successful ingest + active subscription + no job already queued/running); the worker resolves each tenant's **last successful ingest payload at run time** (not enqueue time) and replays it under a **preserve-mode** seed (`writeback_ledger` + `kill_switches` survive; `recommendations`/`part_keys`/`part_contexts` are replaced); ingest history labels these runs "Scheduled recompute" vs "Upload".
  - **Two most significant review catches:** (1) Task 4's middleware fix closed a **cross-tenant fallthrough** — the pre-C5 `expected is not None and` skip, safe only because an unconfigured tenant had no store, would have become a live cross-tenant data read (tenant-A token → tenant-B data) the moment stores started resolving dynamically; the reviewer enumerated all six paths through the gate and proved every one fails closed. (2) Task 10's worker-handler fix closed a **race-condition data-reversion bug**: resolving the recompute payload on one connection while `run_ingest` seeded on another meant a user upload committing in that window could be **silently reverted** by an older recompute payload; fixed with an atomic supersede-check that re-queries the latest ingest id under the same per-tenant advisory lock the seed itself takes, so nothing can commit between the check and the seed.
  - **Tests:** agent-spine `bff`+`pg` suites reached **391 passed / 1 skipped**; `apps/web` reached **378 Vitest tests**, build + lint clean.
  - **Carry-forwards** (full list with per-task minors in TASKS.md): queue reconciliation across recomputes (spec §3.6, deliberate — upgrade path documented); registry cache eviction/TTL (YAGNI at current tenant counts); per-tenant recompute cadence/opt-out (one global nightly schedule in v1).
  - **Live rollout is NOT done.** Code-complete only; [deploy/C5_ROLLOUT.md](deploy/C5_ROLLOUT.md) (7 steps + a prerequisite that checks whether C4 is even live yet, since this repo's own trackers disagree with the design doc's assumption) still needs to run: `db push` 0013–0014, BFF/worker redeploy, `apps/web` redeploy + `VITE_TENANT_SLUGS` removal from Vercel, pg_cron enable + schedule, and the acceptance gate (a brand-new signup reaching recommendations with zero manual steps).

**Commercial track exit:** self-serve signup → upload → recommendation → approve, live at a brand domain, Stripe billing active.

- [x] Site explainer redesign — apps/site homepage rebuilt as parent-brand (aeronta.com) interactive explainer (WorkbenchDemo + SavingsEstimator islands, restyled shell) ✅ 2026-07-28

---

## Phases 2–6 — Post-v1 Roadmap (Backlog)

**Source of truth:** design [§8 Phased roadmap](docs/design/2026-04-14-trax-io-inventory-optimizer-design.md) (phase content + SKUs) and [§9 Risks](docs/design/2026-04-14-trax-io-inventory-optimizer-design.md) (pre-work gates). Each phase **adds exactly one specialist** to the existing Supervisor spine without re-architecting it — the payoff for the hierarchical-from-day-1 design ([design §3.1](docs/design/2026-04-14-trax-io-inventory-optimizer-design.md)). Sequencing/priority (P2/P3) firms up when the v1 lighthouse exits shadow mode; the feature lists below are the buildable breakdown per phase.

| Phase | Commercial SKU | Specialist added | Depends on | Headline value |
|---|---|---|---|---|
| **v2** | Trax IO Causal | Causal Demand Forecaster | v1 | Forward flight plans → materially better ROP for flying-program parts |
| **v3** | Trax IO AOG Shield | AOG Risk | v1 (v2 sharpens it) | Predict shortages N days out; sells "AOG hours prevented" |
| **v4** | Trax IO Recovery | Excess & Redistribution | v1 | Recover trapped capital: excess / obsolete / redistribution |
| **v5** | Trax IO Sourcing | Sourcing | **v2 + v3** | Optimal repair-vs-buy route per demand event |
| **v6** | Trax IO Network | Rotable Pool | v5 (+ v2/v3) | Multi-echelon METRIC rotable pool sizing — the moat |

### v2 — Causal Demand Forecasting · SKU "Trax IO Causal"
**Adds** the Causal Demand Forecaster specialist. **Objective shift:** forward-looking causal demand replaces v1's historical-projection baseline feeding the Policy Engine.
- [ ] Causal Demand Forecaster specialist subagent (7th on the spine; Supervisor contract unchanged)
- [ ] Forward flight-plan ingestion path — new lane from OCC / commercial scheduling (IFS / Sabre / Amadeus); **not in the v1 extract domains** → new source connector + wire schema + `forward_flight_plan` feature group
- [ ] Fleet-composition-change ingestion + effectivity handling
- [ ] `eo_published` event consumption wired into the causal forecaster
- [ ] Forward-looking demand-distribution model replacing the historical baseline in the Policy Engine input contract (confidence-gated fallback to the v1 baseline)
- [ ] Turn on causal covariates in forecasting (flight hours / cycles / wash rate — `causal_utilization` / `wash_rate` stubbed & unused in v1)
- [ ] Federated peer-benchmark "peer median" **product surface** + entitlement/packaging as a premium SKU (isolation infra built in v1; [design §5](docs/design/2026-04-14-trax-io-inventory-optimizer-design.md))
- [ ] BVR: causal-uplift attribution block
- [ ] **Pre-work gate:** per-tenant scoping of the forward-flight-plan source system + integration pattern before v2 can ship (design §9) → ADR-0016

### v3 — AOG & Shortage Risk · SKU "Trax IO AOG Shield"
**Adds** the AOG Risk specialist. **Tier A (advisor-only) for all v3 recommendations.** Shifts the commercial conversation from "dollars saved" to "AOG hours prevented."
- [ ] AOG Risk specialist subagent (every v3 rec routed through Tier A human approval)
- [ ] Open WO / EO event scanning (work-order / engineering-order feeds) into the risk model
- [ ] N-days-forward shortage-prediction model over open WO/EO + current stock + open orders + vendor performance + forecasts
- [ ] Per-tail AOG-risk scoring
- [ ] Recommendation types: expedite · transfer · interchangeable substitution · vendor switch
- [ ] Per-tenant AOG cost model (per-tenant constant + consulting calibration engagement)
- [ ] "AOG hours prevented" value metric surfaced in the BVR
- [ ] (v3.5 research) automated AOG-cost-model calibration
- [ ] **Pre-work gate:** per-tenant AOG cost-model calibration — quality directly determines phase-3 recs (design §9) → ADR-0017

### v4 — Excess, Obsolete & Redistribution · SKU "Trax IO Recovery"
**Adds** the Excess & Redistribution specialist. Commercial model carries a **variable component tied to realized excess reduction.**
- [ ] Excess & Redistribution specialist subagent
- [ ] Detectors: slow-movers · idle rotable-pool inflation · dead stock at outstations · shelf-life-expiring inventory
- [ ] Station-to-station redistribution recommender
- [ ] Return-to-vendor / core-exchange recommender
- [ ] Phase-out recommender
- [ ] Third-party-sale recommender using the `CUSTOMER_ORDER_*` demand signal (new ingestion + feature group; treated as noise in v1)
- [ ] Enable **AgentCore Code Interpreter** for planner-driven scenario math (first phase to use it)
- [ ] Realized-excess-reduction measurement for the variable commercial component (BVR extension)

### v5 — Repair-vs-Buy / Sourcing · SKU "Trax IO Sourcing"
**Adds** the Sourcing specialist. **Hard dependency on v2 (forward demand) + v3 (AOG urgency).**
- [ ] Sourcing specialist subagent
- [ ] Route optimizer across: new PO · repair RO · interchange · rental · loan · pool-exchange · cannibalization
- [ ] Vendor-terms model over `PN_VENDOR_PRICE` (price / condition / lead-time)
- [ ] Inputs: wash rate · repair cost · criticality · open orders
- [ ] v3 AOG-urgency signal integration (dependency)
- [ ] v2 forward-demand integration (dependency)
- [ ] **Pre-work gate:** repair-TAT + repair-cost data source — **no such source exists in the v1 extract registry today** (see #12 Wave A limitation); must be scoped/added to enable the repair-RO route → ADR-0018

### v6 — Rotable Pool Sizing (Multi-Echelon METRIC) · SKU "Trax IO Network"
**Adds** the Rotable Pool specialist. Premium tier / multi-year contract anchor. Replaces v1's proto-multi-echelon (main = base-stock, outstation = emergency) with full multi-echelon optimization.
- [ ] Rotable Pool specialist subagent
- [ ] METRIC / VARI-METRIC multi-echelon optimizer across the main + outstation hierarchy
- [ ] Realistic TAT-distribution modeling (**depends on the repair-TAT source from the v5 pre-work gate**)
- [ ] Interchangeability-group-aware pooling
- [ ] Cannibalization-policy modeling
- [ ] Fleet-plan input
- [ ] Discrete-event simulator (SageMaker + custom container)
- [ ] Rotable loan-pool ingestion — `LOAN_CATEGORY` in `PN_INVENTORY_DETAIL` (deferred from v1 to v6)

### Cross-phase foundations (built or scoped in v1)
Phases 2–6 build on v1 investments rather than rebuilding them: the hierarchical spine (one specialist added per phase), the Essentiality Mapping service, the eMRO Outbound Event Publisher, the tenant onboarding runbook, the SOC 2 Type II program, the ML-ops platform (champion/challenger, red-team suite, model registry, drift detection), and the federated peer-benchmark layer (isolation infra in v1, product-surfaced in v2). See [design §8 "Cross-phase platform investments"](docs/design/2026-04-14-trax-io-inventory-optimizer-design.md).

### Pre-work gates (clear before the phase can ship)
| Gate | Blocks | Why |
|---|---|---|
| Forward-flight-plan source + integration pattern (per tenant) | v2 | Not covered by the v1 extract domains; source system varies per customer (design §9) |
| Per-tenant AOG cost model calibration | v3 | Directly determines phase-3 recommendation quality; consulting until v3.5 automation (design §9) |
| Repair-TAT / repair-cost data source | v5, v6 | No source in the extract registry today (#12 Wave A); required for the repair-RO route and METRIC TAT distributions |

### Future ADRs (numbered from the next free slot — latest shipped is [0016](docs/adr/2026-07-07-0016-emro-writeback-slice2.md))
- [ ] ADR-0017 — Forward-flight-plan ingestion & per-tenant source scoping (pre-v2)
- [ ] ADR-0018 — AOG cost-model calibration methodology (pre-v3)
- [ ] ADR-0019 — Repair-TAT / repair-cost data source (pre-v5; prerequisite for v6 TAT modeling)
- [ ] ADR-0020 — Federated cross-tenant feature-pipeline isolation model (peer-benchmark productization, v2)
- [ ] ADR-0021 — Multi-echelon (METRIC) simulator architecture (v6)

> **Explicit non-goals through v6** ([design §8](docs/design/2026-04-14-trax-io-inventory-optimizer-design.md)): not a replacement for eMRO's planning/procurement UIs (it recommends; eMRO records & executes) · not a general-purpose MRO chatbot · does not *generate* commercial flight schedules (only consumes them) · not an ERP replacement (write surface is `PN_INVENTORY_LEVEL` only) · not open-source.
