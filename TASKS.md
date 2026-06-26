# Tasks

## Current Session — 2026-04-17

### In Progress
- Nothing — the deterministic Recommendation Engine (#11) shipped, fully built + tested.

### Completed This Session
- [x] Ran `/init-project`: scaffolded CLAUDE.md, ROADMAP.md, TASKS.md, and `.claude/` workspace (`skills/`, `agents/`, `memory/lessons.md`)
- [x] Mapped roadmap from [docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md](docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md) into ROADMAP.md
- [x] Anchored CLAUDE.md to the authoritative design / roadmap / plans layout
- [x] Sub-project #1 Phase 1 scaffold: `tools/nightly-extract/` (Click CLI skeleton, 12 SQL placeholder files, `uv` + `pytest` + `ruff`, README + smoke test)
- [x] Sub-project #9 Phase 1 scaffold: `infra/observability-soc2/` (CDK Python stack — CloudTrail Lake 7-yr, Audit Manager SOC 2 assessment, per-tenant KMS CMKs w/ annual rotation, S3 Object Lock Compliance 7-yr, OTel collector Fargate placeholder, per-tenant log groups; `uv` + `pytest` synth tests + `ruff`; `README.md` + `docs/soc2-onboarding.md`)
- [x] Sub-project #2 Phase 1 scaffold: `services/feature-store/` (FeatureStoreClient Protocol w/ `TenantContext`, `InMemoryFeatureStore` stub per ADR-0002, 10 pydantic feature-group schemas, `uv`+`pytest`+`ruff`) + `infra/feature-store/` (CDK stack: per-tenant KMS, S3 landing/lake, Glue DB, DynamoDB online layer, 10 Iceberg column schemas, synth tests)
- [x] Saved project memory: AWS account name is `TraxAi` (not to be confused with product name "Trax IO")
- [x] Verified Phase 1 scaffolds: all 4 test suites green (32 tests total — 3 + 16 + 8 + 5) — 2026-04-16
- [x] Fixed `infra/observability-soc2` CDK bug: `tags_raw=` → `tags=` with `CfnTag` objects on `CfnEventDataStore`
- [x] Drafted [ExtractManifest contract](docs/contracts/2026-04-17-extract-manifest-contract.md) — 21 canonical domains, bind-var map, atomicity rules, manifest v1.0.0 schema — 2026-04-17
- [x] Rewrote `tools/nightly-extract/` for 21 real domains (from `~/Downloads/PTC Project Files/eMRO Data SQLs.sql`): bind-var parameterized SQLs, `ExtractManifest` pydantic model, domain registry, 45 tests green — 2026-04-17
- [x] **Wave 0 Phase 2 slice** (3 parallel subagents, 152 tests green total) — 2026-04-17
  - **#1 nightly-extract** (73 tests): `oracle.py` (oracledb thin driver + context mgr), `binds.py` (contract-driven bind resolver), `runner.py` (per-domain atomicity + manifest emission + source_sql_sha256), CLI `--dry-run/--no-dry-run`, ULID run_ids
  - **#2 feature-store** (28+11 tests): PySpark `demand_history` Glue job (manifest-driven, Iceberg writer, ANSI-safe casts, `unionByName(allowMissingColumns=True)`, lazy `awsglue` import), CDK Glue job packaging w/ tenant-scoped IAM role (Glue 4.0, G.1X × 2)
  - **#9 observability-soc2** (40 tests): multi-tenant refactor — `TenantSpec` frozen dataclass + `LIGHTHOUSE_TENANTS` + `load_tenants_from_env()`, per-tenant KMS/log-group loop, `CfnOutput` contract (`TraxIo-<tenant>-TenantKmsArn`, `TraxIo-<tenant>-TenantLogGroupArn`), `iam_helpers.tenant_tag_condition()` + `apply_tenant_tags()`
- [x] **Sub-project #11 — Recommendation Engine (deterministic v1)** brainstormed → spec'd → planned → **built end-to-end** (`services/recommendation-engine/`, 123 tests green, ruff clean) — 2026-04-17
  - Flow: `/brainstorming` → [spec](docs/superpowers/specs/2026-04-17-trax-io-recommendation-engine-design.md) (recon + 4-lens adversarial review, all approve-with-fixes, every finding folded in) → [plan](docs/superpowers/plans/2026-04-17-trax-io-recommendation-engine.md) → [ADR-0004](docs/adr/2026-04-17-0004-deterministic-recommendation-layer.md) + roadmap amendment (register 10→11)
  - Engine: net-position core; deterministic regime classifier; demand projector (per-day rate + compound-Poisson/NBD params); policy engine ((S−1,S)/(s,S)/(R,Q) + numeric quantile path + §6.2 constraints); 5 recommenders (Adjust/Purchase/Transfer/Reduce/Sell); arbitration (transfer-before-purchase, no contradictions); AOG risk scorer (part-class recovery time); confidence + deterministic ranking; content-addressed provenance ids
  - Surfaces: library facade, `trax-io-reco` click CLI, optional FastAPI read API (behind `api` extra)
  - Forward-compatible contract mirrors (`Regime`/`CanonicalCriticality`/`PolicyKind`/`AutonomyTier`/`ForecastHorizon`/`PolicyRecommendation`) for promotion to Agent Spine #4
  - **4-lens adversarial code review** (correctness / spec-compliance / determinism / test-adequacy) after build — found 1 critical + several major bugs, **all fixed + locked with 12 regression tests** (135 tests total): interchange both-short over-buy (rep-member fix), one-way-interchange collapse, `apportion()` zero-consumption over-allocation, bucket-blind variance scaling, Decimal-scale hash non-invariance, order-dependent vendor resolution, banker-rounding flips (`round_half_up`), negative-unit_cost guard, zero-qty residual purchase, ranking criticality weighting
  - Lesson captured: uv editable path deps (cross-project AND the workspace's own console script) don't reliably expose src-layout packages → non-editable path source / `--reinstall-package` + `-m`

### Blockers / cross-agent contracts
- ~~**#1 ↔ #2 contract:** `ExtractManifest` pydantic model~~ — **RESOLVED 2026-04-17** → [contract](docs/contracts/2026-04-17-extract-manifest-contract.md) + implementation in `tools/nightly-extract/src/trax_io_extract/manifest.py`. 21-domain list is now canonical (matches customer's `eMRO Data SQLs.sql`).
- ~~**Oracle package dependencies:**~~ **RESOLVED 2026-04-17** — product decision: inline the PL/SQL package logic into the extract SQLs so we have zero dependency on `PKG_TRAX_PTC` or `pkg_settings_pn_master`. Reference package body kept at `~/Downloads/PTC Project Files/PKG_TRAX_PTC.sql`. Inlined functions: `getKitCost` + `getRecordsType` (in `15_part_master.sql`); `getPNCategory` (in `03_demand_history_expendables.sql` as direct `pn_master.category` lookup).
- **All sub-projects ↔ #9:** new contract published at [infra/observability-soc2/docs/soc2-onboarding.md](infra/observability-soc2/docs/soc2-onboarding.md) — mandatory reading before any other sub-project's first PR. Defines tagging, per-tenant KMS envelope, audit-log schema, OTel span attrs, PII redaction, IAM condition keys, audit mirror.
- **Cedar policy templates** (design §3.2) not yet drafted — will block tenant-scoped IAM in Phase 2.
- **Bucket naming convention** (`trax-io-<tenant>-<env>-landing/lake`) — decide before first #2 deploy; CDK currently auto-names.
- **ADR-0002 alignment:** canonical `InMemoryFeatureStore` lives in Agent Spine repo (not yet scaffolded). Reference stub shipped in `services/feature-store/` to unblock; move Protocol to Spine when Spine lands.

### Next Session
- **#11 follow-ups:** promote the on-hand-stock / current-policy / scheduled-demand / AOG / repair-TAT stubs into Feature Store #2 read methods; extend interchange rollup to full-network apportionment + one-way-directed transfer donors; fix the `stock_level_upload` #19 PN/LOCATION alias transposition in the extract SQL
- **#1 Phase 2 finish:** S3 landing writer (parquet per domain + manifest upload), real Oracle smoke test against customer staging, begin 14-day clean-run watch
- **#2 Phase 2 expand:** extend Glue job pattern from `demand_history` to the remaining 20 domains; finalize Iceberg schemas; start 24-month backfill for lighthouse
- **#9 Phase 2 finish:** deploy CloudTrail Lake + Audit Manager to TraxAi account; provision per-tenant KMS w/ annual rotation; wire OTel → X-Ray for first real agent hop
- **Cross-cutting:** draft Cedar policy templates (design §3.2) — blocker for tenant-scoped IAM
- **Decision needed:** bucket naming convention (`trax-io-<tenant>-<env>-landing/lake`) before first #2 deploy
- Confirm whether the lighthouse customer is signed (Week 0 milestone in [ROADMAP.md](ROADMAP.md))
