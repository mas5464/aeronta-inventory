# Trax IO Feature Store — service

Phase 1 scaffold for sub-project #2 (Feature Store & Data Lake).

This package ships the **read-side** contract for the Trax IO Feature Store:

- `FeatureStoreClient` — typed `Protocol` that the Agent Spine (sub-project #4) depends on.
- `InMemoryFeatureStore` — dict-backed reference implementation per [ADR-0002](../../docs/adr/0002-in-memory-feature-store-stub.md). Allows the Spine and lighthouse shadow-mode pilot to run before the production Iceberg + DynamoDB backend ships.
- Pydantic schemas for the 10 v1 feature groups (design §4.2): `demand_history`, `causal_utilization`, `lead_time_distribution`, `wash_rate_history`, `vendor_economics`, `part_attributes`, `criticality`, `interchangeable_graph`, `location_graph`, `open_orders_snapshot`.

The production backend (`GlueIcebergFeatureStore`) lands in Phase 6 of the plan and conforms to the same `FeatureStoreClient` Protocol so the swap is a one-line DI change.

## Tenant isolation — the chokepoint

Every `FeatureStoreClient` method **requires** a `TenantContext` kwarg. Omitting it raises `MissingTenantContextError`; cross-tenant reads raise `FeatureStoreLookupError`. The enforcement lives at the client interface so downstream specialists cannot route around it. In production, the Cedar principal is additionally verified against `tenant.tenant_id`.

## Dev setup

```bash
cd services/feature-store
uv sync --extra dev
uv run pytest
uv run ruff check .
```

## Layout

```
services/feature-store/
├── pyproject.toml
├── README.md
├── src/trax_io_feature_store/
│   ├── __init__.py
│   ├── client.py              # Protocol + InMemoryFeatureStore + TenantContext
│   └── schemas/
│       ├── __init__.py
│       └── features.py        # 10 feature-group pydantic models
└── tests/
    ├── test_schemas.py        # one per feature group
    └── test_client.py         # tenant-isolation + Protocol conformance
```

## Glue transforms (Phase 2)

PySpark Glue ETL jobs under `src/trax_io_feature_store/glue/` consume raw
nightly-extract artifacts described by an `ExtractManifest` and produce rows
for one feature group. Shipped so far:

- `demand_history_job` — rotable removals + expendable issues, bucketed by day.
- `stock_position_job` — `stock_amount` #18 → on-hand / serviceable / in-repair / allocated / rental / loan.
- `current_policy_job` — `stock_level_upload` #19 → ROP / EOQ / safety stock / max / replenishment lead.
- `vendor_economics_job` — `pn_vendor_price` #16 (+ `part_master` #15 costs) → per-vendor rows **plus** a synthesized `vendor="DEFAULT"` canonical row (preferred-then-cheapest), so the assembler resolves both its open-order-vendor path and its fallback.
- `part_attributes_job` — `part_master` #15 → part_class / shelf-life / hazmat / tool-control / tail count (mirrors the reco bridge derivations).
- `criticality_job` — `part_master` #15 → raw essentiality code + canonical 1–5 tier (shared default essentiality map, §4.3).

These six cover **every required engine input** (`demand_history`, `stock_position`,
`current_policy`, `vendor_economics`, `part_attributes`, `criticality`) plus stock/policy.

The four **derived / graph** groups complete v1 materialization:

- `lead_time_distribution_job` — `pn_vendor_price` #16 (preferred-vendor promised lead) + `order_plan_closed_orders` #7 (realized lead days) → mean + **index-based** p50/p90/p99 exactly matching the bridge `_lead_time` (not `percentile_approx`); promised-only fallback when there's no realized history.
- `open_orders_snapshot_job` — `order_plan` #8 (OPEN) → one snapshot per (pn, location) with the per-order `array<struct>` (sorted, deterministic) + `total_open_qty`.
- `interchangeable_graph_job` — `part_chain_details` #11 → per-PN `members` + `edges` rollup (each edge attached to both endpoints), `group_id = "+".join(sorted(members))`.
- `location_graph_job` — `location_master` #5 → `role` (main/outstation) + parent; `children` left empty to match the bridge.

All ten v1 feature groups the engine reads now have a Glue materialization job.

`glue/_common.py` holds the shared `load_manifest` / `select_artifacts` /
`read_artifacts` / `append_iceberg` helpers the single-domain jobs reuse, plus the
**cast-fidelity** helpers every transform uses: `disable_ansi_mode(spark)` (pins
`spark.sql.ansi.enabled=false` to match Glue 4.0, so a malformed extract value yields null
instead of crashing the job) and `coerce_int(col, default)` (`bround`→int, HALF_EVEN, so
string numerics *round* like the reco bridge's `_i` rather than truncate). The extract
delivers every numeric as a string, so tests feed string-typed inputs and the conftest
SparkSession also pins ANSI off. The
transform helpers are pure PySpark functions, unit-tested against a local
SparkSession (skipped if Java/Spark is absent). The CDK stack packages every job
via `_make_glue_job(feature_group=…)`.

Data flow (`demand_history_job`):

```
manifest.json  --(select_demand_artifacts)-->  raw JSON artifacts
              --(read_raw + transform_to_feature_group)-->  demand_history rows
              --(write_iceberg)-->  glue_catalog.trax_io.demand_history
                                    partitioned by (tenant_id, extract_date)
```

Local invocation shape (when running inside a Glue 4.0 container):

```bash
spark-submit demand_history_job.py \
  --tenant_id aircanada \
  --extract_date 2026-04-16 \
  --landing_bucket trax-io-aircanada-landing \
  --lake_bucket   trax-io-aircanada-lake \
  --manifest_s3_uri s3://trax-io-aircanada-landing/extract_date=2026-04-16/run_id=.../manifest.json
```

The transform helpers are pure PySpark functions and have unit tests under
`tests/glue/`. Spark-requiring tests skip cleanly when `pyspark` / Java
are not available locally.

Only `causal_utilization` and `wash_rate_history` remain unmaterialized — neither is
consumed by the v1 deterministic engine, so they are deferred.

## Out of scope for Phase 1

- No Iceberg writes (Phase 2 ingest Glue job).
- No DynamoDB online layer (Phase 4).
- No `GlueIcebergFeatureStore` implementation (Phase 6).
- No contract test package shared with sub-project #4 (Phase 6 task 24).
