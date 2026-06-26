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

## Glue transforms (Phase 2 template slice)

Phase 2 ships the first of ten PySpark Glue ETL jobs under
`src/trax_io_feature_store/glue/`. Each job consumes raw nightly-extract
artifacts described by an `ExtractManifest` and produces rows for one
feature group.

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

The remaining nine feature-group jobs (`causal_utilization`,
`lead_time_distribution`, ...) will ship as sibling modules following the
same pattern.

## Out of scope for Phase 1

- No Iceberg writes (Phase 2 ingest Glue job).
- No DynamoDB online layer (Phase 4).
- No `GlueIcebergFeatureStore` implementation (Phase 6).
- No contract test package shared with sub-project #4 (Phase 6 task 24).
