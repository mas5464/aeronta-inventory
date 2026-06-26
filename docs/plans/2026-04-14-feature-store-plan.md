# Sub-plan #2 — Feature Store & Data Lake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the multi-tenant feature store that ingests sub-plan #1's nightly Parquet extracts and the sub-plan #3 event stream, materializes versioned Iceberg tables in the Trax data lake, populates a DynamoDB online layer for sub-10ms reads at event-time, and exposes the agreed `FeatureStoreClient` Protocol so sub-plan #4's Agent Spine can swap from its in-memory fake to the real backend with zero code change.

**Architecture:** Daily nightly extracts land in tenant-scoped S3 prefixes; Glue jobs validate manifests, transform Parquet to Iceberg, compute derived feature tables, and update the DynamoDB online layer. Event lane (sub-plan #3) writes to Kinesis → Iceberg CDC tables and pushes incremental updates to DynamoDB. Iceberg time-travel preserves any historical recommendation's exact input — non-negotiable for SOC 2 and for planner trust.

**Tech Stack:**
- AWS Glue 4.0 (Spark 3.3) for batch ETL
- Apache Iceberg via Glue Catalog
- Amazon S3 (data lake) with Object Lock on the audit subset
- Amazon DynamoDB (online features)
- Amazon Kinesis Data Streams (event lane fan-in)
- AWS KMS per-tenant CMKs
- Python 3.12 + `pyiceberg` + `boto3` for the read-side `FeatureStoreClient` implementation
- Terraform for infrastructure (Glue jobs, IAM, KMS, S3, DynamoDB tables)
- `pytest` + `moto` + `pyiceberg` local catalog for tests
- Repo: `trax-io-feature-store`

**Dependencies:**
- **#1 Extract Utility** — produces the daily Parquet + manifest.
- **#3 Event Publisher** — produces the event stream.
- **#4 Agent Spine** — owns the `FeatureStoreClient` Protocol (this sub-plan implements it).

---

## File Structure

```
trax-io-feature-store/
├── pyproject.toml
├── README.md
├── glue_jobs/
│   ├── ingest_nightly.py            # Validate manifest, Parquet → Iceberg
│   ├── derive_features.py           # Compute derived feature tables
│   ├── update_online.py             # Iceberg → DynamoDB online layer
│   └── event_cdc.py                 # Kinesis → Iceberg CDC
├── src/trax_io_feature_store/
│   ├── __init__.py
│   ├── client.py                    # GlueIcebergFeatureStore (impl of #4 Protocol)
│   ├── online.py                    # DynamoDB read wrapper
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── raw.py                   # Iceberg schemas matching #1 manifests
│   │   └── derived.py               # Derived feature schemas
│   ├── lineage.py                   # OpenLineage emitters
│   └── manifest.py                  # ExtractManifest reader (matches #1)
├── infra/
│   ├── main.tf
│   ├── tenant_module/
│   │   ├── kms.tf
│   │   ├── s3_landing.tf
│   │   ├── s3_lake.tf
│   │   ├── glue_database.tf
│   │   ├── glue_jobs.tf
│   │   ├── dynamodb.tf
│   │   ├── kinesis.tf
│   │   └── iam.tf
│   └── shared/
│       ├── audit_account.tf
│       └── observability.tf
├── tests/
│   ├── unit/
│   │   ├── test_manifest_reader.py
│   │   ├── test_schemas.py
│   │   ├── test_online_client.py
│   │   ├── test_iceberg_client.py
│   │   └── test_lineage.py
│   ├── integration/
│   │   ├── conftest.py              # local Iceberg + LocalStack DynamoDB
│   │   └── test_end_to_end_ingest.py
│   ├── contract/                    # SHARED with sub-plan #4
│   │   └── test_feature_store_protocol.py  # runs against InMemory + GlueIceberg
│   └── fixtures/
│       ├── extracts/                # sample Parquet from #1
│       └── manifests/
└── docs/
    ├── ARCHITECTURE.md
    ├── TENANT_ONBOARDING.md
    └── adr/
        └── 0001-iceberg-vs-deltalake.md
```

---

## Phase Plan

| Phase | Scope | Tasks |
|---|---|---|
| 0 | Repo bootstrap, infra-as-code skeleton | 1–3 |
| 1 | Iceberg raw schemas (one per #1 SQL output) | 4–6 |
| 2 | Manifest reader + nightly ingest Glue job | 7–10 |
| 3 | Derived feature tables (causal, wash rate, lead time, demand history) | 11–15 |
| 4 | DynamoDB online layer + Iceberg→DynamoDB job | 16–18 |
| 5 | Event lane: Kinesis → Iceberg CDC + online update | 19–22 |
| 6 | `GlueIcebergFeatureStore` client (impl of Protocol) + shared contract tests | 23–27 |
| 7 | Lineage (OpenLineage) + SOC 2 hooks | 28–30 |
| 8 | Terraform per-tenant module + multi-tenant deploy | 31–34 |
| 9 | Backfill + replay tooling | 35–37 |

---

## Phase 0: Bootstrap

### Task 1: Initialize repo

```bash
mkdir trax-io-feature-store && cd trax-io-feature-store
git init && uv init --python 3.12 --package
```

`pyproject.toml`:
```toml
[project]
name = "trax-io-feature-store"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "pyiceberg[s3fs,glue,pyarrow]>=0.7.0",
  "boto3>=1.34.0",
  "pyarrow>=16.0.0",
  "pydantic>=2.7.0",
  "structlog>=24.1.0",
  "openlineage-python>=1.15.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2.0",
  "pytest-cov>=5.0.0",
  "moto[all]>=5.0.0",
  "ruff>=0.4.0",
  "mypy>=1.10.0",
  # Pinned Spark for local Glue-script tests
  "pyspark>=3.3.0",
]
glue = [
  # Glue 4 packages already installed in Glue runtime; this group is for
  # local lint/typecheck only.
  "boto3>=1.34.0",
]
```

### Task 2: Terraform skeleton + remote state

`infra/main.tf` with S3 backend per environment, AWS provider pinned, tenant_module instantiation by `for_each` over tenant list.

### Task 3: CI: ruff + mypy + pytest + terraform validate + glue script lint

GitHub Actions matrix runs Python tests in one job, `terraform validate` and `terraform plan` in a second job (against a locked-down sandbox account).

---

## Phase 1: Iceberg raw schemas

### Task 4: Schema-from-manifest generator

**Files:** `src/trax_io_feature_store/schemas/raw.py`, `tests/unit/test_schemas.py`

The 21 query outputs from sub-plan #1 each get a corresponding Iceberg schema. The schema-from-manifest generator reads sub-plan #1's manifest schema, infers PyArrow types, and produces a `pyiceberg.schema.Schema` with explicit nullability per column.

- [ ] **Failing test** that asserts the `causal_values` schema has columns `(tenant_id, host_product_id, host_loc_id, host_causal_minutes, causal_cycles, start_date, end_date, ingested_at, source_extract_date)`.
- [ ] **Implement** schema dictionary keyed by query name; each schema includes the four "operational" columns (`tenant_id`, `ingested_at`, `source_extract_date`, `manifest_sha256`) added by the ingest job.
- [ ] **Property test** that every #1 query name has a matching Iceberg schema (drift detector).

### Task 5: Iceberg table creation Glue script

`glue_jobs/init_tables.py` — runs once per tenant during onboarding; creates Iceberg tables under `trax_io_lake.{tenant_id}.raw_*` with `partitioning = [tenant_id, source_extract_date]` and `format-version = 2` for time-travel.

### Task 6: Schema evolution test

Test that adding a nullable column to a #1 SQL output (sub-plan #1 v1.1) is backward-compatible: the Iceberg ingest still works against old-shaped Parquet without manual intervention.

---

## Phase 2: Manifest reader + nightly ingest

### Task 7: Manifest reader

**Files:** `src/trax_io_feature_store/manifest.py`, `tests/unit/test_manifest_reader.py`

`ExtractManifest.load(s3_path)` reads sub-plan #1's `manifest.json`, validates against pydantic schema, computes total row count, exposes per-query metadata.

### Task 8: Nightly ingest Glue job

**Files:** `glue_jobs/ingest_nightly.py`

```python
# glue_jobs/ingest_nightly.py
"""Glue job: nightly ingestion of one tenant's Parquet extract into Iceberg.

Trigger: EventBridge rule on s3:ObjectCreated for tenant landing prefix
when manifest.json appears.

Args:
  --tenant_id
  --extract_date
  --manifest_s3_uri
"""
from __future__ import annotations
import sys
from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from pyspark.sql import SparkSession
from pyspark.sql.functions import lit, current_timestamp


def main():
    args = getResolvedOptions(sys.argv, ["tenant_id", "extract_date", "manifest_s3_uri"])
    spark = SparkSession.builder.appName("trax-io-ingest").getOrCreate()
    glue = GlueContext(spark.sparkContext)

    # 1. Read + validate manifest
    manifest = read_manifest(args["manifest_s3_uri"])
    assert manifest.tenant_id == args["tenant_id"]

    # 2. For each query in manifest, validate sha256 + ingest
    for entry in manifest.entries:
        df = spark.read.parquet(entry.s3_uri)
        # Validate row count
        assert df.count() == entry.row_count, (
            f"row count mismatch for {entry.query}: "
            f"manifest={entry.row_count} actual={df.count()}"
        )
        # Add operational columns
        df = (df
              .withColumn("tenant_id", lit(args["tenant_id"]))
              .withColumn("source_extract_date", lit(args["extract_date"]).cast("date"))
              .withColumn("manifest_sha256", lit(manifest.sha256))
              .withColumn("ingested_at", current_timestamp())
              )
        # Append to Iceberg
        target = f"glue_catalog.trax_io_lake.{args['tenant_id']}_raw_{entry.query}"
        df.writeTo(target).append()

    # 3. Emit OpenLineage event
    emit_lineage(manifest)

    # 4. Trigger downstream derive_features job
    trigger_step_function("derive-features", tenant_id=args["tenant_id"], extract_date=args["extract_date"])


if __name__ == "__main__":
    main()
```

### Task 9: Quarantine bad extracts

If row count or SHA-256 mismatch, write the manifest to a quarantine S3 prefix, alert via SNS, and do NOT append to Iceberg. Test asserts quarantine path is hit on bad input.

### Task 10: Idempotent re-ingestion

If the same `manifest_sha256` was already ingested, skip. Test asserts double-runs do not duplicate rows.

---

## Phase 3: Derived feature tables

The 12 v1 SQLs deliver raw eMRO state. The Agent Spine actually consumes *derived* features (wash rate trend, lead-time distribution, etc.). Phase 3 turns raw into derived.

### Task 11: `wash_rate_history` derived table

Recompute the wash rate formula from §4.3 of the design (`(RO/CREATE − RO/RECEIVING) / RO/CREATE`) per `(tenant_id, pn, location, month)`, store the trend not just the point.

### Task 12: `lead_time_distribution` derived table

For each `(tenant_id, pn, vendor, condition)`:
- Promised lead time from `pn_vendor_price.lead_days`.
- Realized lead time from closed orders (`actual_rcv_date - plan_order_date`).
- Empirical mean, p50, p90, p99, and the *promised-vs-actual delta distribution*.

The promised-vs-actual delta is the highest-signal feature for safety stock per the design.

### Task 13: `demand_history` aggregated table

Roll removals + issues per `(tenant_id, pn, location, day, week, month)`. Honor interchangeability rollup at the `interchange_group` level.

### Task 14: `causal_utilization` derived table

Flight hours and cycles per `(ac_type, destination, day)`, joined with demand observations to enable causal forecasting in v2.

### Task 15: `interchange_graph` materialized view

Resolve `pn_interchangeable` + `pn_interchg_one_way` into a per-tenant graph keyed by `pn → group_id` and `group_id → members[]`. Honor one-way chains.

---

## Phase 4: DynamoDB online layer

### Task 16: DynamoDB schema

One table per tenant: `trax-io-online-{tenant_id}` with:
- PK: `pk` = `"PL#{pn}#{location}"`
- SK: `sk` = `"FEATURES"`
- Attributes: `latest_demand_history` (compressed JSON), `current_stock`, `open_order_qty`, `last_forecast` (compressed JSON), `last_policy` (compressed JSON), `regime`, `criticality`, `updated_at`.

Tenant-scoped KMS key. Point-in-time recovery enabled.

### Task 17: Iceberg → DynamoDB job

Glue job that reads the latest derived feature row per `(tenant_id, pn, location)` and upserts to the DynamoDB online table. Runs after `derive_features` completes.

### Task 18: Online client wrapper

**Files:** `src/trax_io_feature_store/online.py`, `tests/unit/test_online_client.py`

```python
# src/trax_io_feature_store/online.py
class OnlineFeatureClient:
    def __init__(self, *, table_name: str, dynamodb_client) -> None: ...
    def get_features(self, *, pn: str, location: str) -> dict[str, Any]: ...
    def update_after_event(self, *, pn: str, location: str, patch: dict) -> None: ...
```

Tests use `moto` for DynamoDB. Sub-10ms reads verified in benchmark suite (Task 27).

---

## Phase 5: Event lane

### Task 19: Kinesis stream per tenant

Terraform module provisions per-tenant Kinesis Data Stream `trax-io-events-{tenant_id}` with KMS encryption.

### Task 20: Event endpoint Lambda

A small Lambda behind API Gateway (mTLS) that receives the seven event kinds from sub-plan #3 and writes to the tenant's Kinesis stream. Implements the contract in `docs/contracts/2026-04-14-emro-event-publisher-contract.md`.

### Task 21: Kinesis → Iceberg CDC Glue streaming job

`glue_jobs/event_cdc.py` runs as a Glue streaming job, reads from Kinesis, writes raw events to `{tenant_id}_events_raw` Iceberg table partitioned by event kind and date. Provides the audit + replay surface.

### Task 22: Online layer incremental update from events

A second consumer of the Kinesis stream (Lambda) updates the DynamoDB online table per event:
- `removal_recorded` → increment local demand counter, invalidate forecast.
- `stock_moved` → recompute current stock.
- `vendor_price_changed` → invalidate lead-time distribution cache.
- `eo_published` (criticality=AD) → emit hot-parts recompute trigger to the Agent Spine event lane.

---

## Phase 6: Client + shared contract tests

### Task 23: `GlueIcebergFeatureStore` — implements the Agent Spine `FeatureStoreClient` Protocol

**Files:** `src/trax_io_feature_store/client.py`

```python
# src/trax_io_feature_store/client.py
from __future__ import annotations
from pyiceberg.catalog import load_catalog
from trax_io.specialists.data_retrieval.feature_store import (
    FeatureStoreClient, FeatureStoreLookupError,
)
from trax_io.contracts.demand import DemandHistory
from trax_io.contracts.part import Part, InterchangeGroup
from trax_io.contracts.tenant import EssentialityMapping
from trax_io.identity.context import current_tenant
from trax_io_feature_store.online import OnlineFeatureClient


class GlueIcebergFeatureStore(FeatureStoreClient):
    """Production implementation of the FeatureStoreClient Protocol.

    Hot-path reads use the DynamoDB online layer (sub-10ms).
    Cold-path reads (full demand history, interchange graph) hit Iceberg.
    """

    def __init__(self, *, online: OnlineFeatureClient, catalog_name: str = "glue_catalog") -> None:
        self._online = online
        self._catalog = load_catalog(catalog_name)

    def get_part(self, *, pn: str) -> Part:
        tenant = current_tenant().tenant_id
        features = self._online.get_features(pn=pn, location="META")
        if not features:
            raise FeatureStoreLookupError(f"part not found: {tenant}/{pn}")
        return Part(...)  # construct from features

    def get_demand_history(self, *, pn: str, location: str) -> DemandHistory:
        tenant = current_tenant().tenant_id
        # Cold-path: query Iceberg derived demand table
        table = self._catalog.load_table(f"trax_io_lake.{tenant}_demand_history")
        scan = table.scan(
            row_filter=f"pn == '{pn}' AND location == '{location}'",
        )
        rows = scan.to_pandas()
        if rows.empty:
            raise FeatureStoreLookupError(...)
        # Convert to DemandHistory
        return DemandHistory(...)

    def get_interchange_group(self, *, pn: str) -> InterchangeGroup | None:
        ...

    def get_open_order_qty(self, *, pn: str, location: str) -> int:
        return self._online.get_features(pn=pn, location=location).get("open_order_qty", 0)

    def get_essentiality_mapping(self) -> EssentialityMapping:
        ...
```

### Task 24: Shared contract test suite

The Agent Spine plan committed an `InMemoryFeatureStore`. This task ships the **shared contract test package** that runs the same scenarios against both:

```python
# tests/contract/test_feature_store_protocol.py
"""Shared contract tests — run against InMemory and GlueIceberg implementations.

Pip-installable as `trax-io-feature-store-contract` for use in both repos.
"""
import pytest
from typing import Callable

from trax_io.specialists.data_retrieval.feature_store import (
    FeatureStoreClient, FeatureStoreLookupError, InMemoryFeatureStore,
)
from trax_io_feature_store.client import GlueIcebergFeatureStore


# Parametrize over both implementations
@pytest.fixture(params=["in_memory", "glue_iceberg"])
def fs(request, glue_iceberg_seeded, in_memory_seeded) -> FeatureStoreClient:
    return {
        "in_memory": in_memory_seeded,
        "glue_iceberg": glue_iceberg_seeded,
    }[request.param]


def test_get_part_returns_seeded_data(fs, aircanada_scope):
    with aircanada_scope:
        part = fs.get_part(pn="LRU-CFM56-HPT-BLADE")
    assert part.pn == "LRU-CFM56-HPT-BLADE"


def test_cross_tenant_read_blocked(fs, jetblue_scope):
    with jetblue_scope, pytest.raises(FeatureStoreLookupError):
        fs.get_part(pn="LRU-CFM56-HPT-BLADE")  # Air Canada part


def test_demand_history_observation_count_matches_seed(fs, aircanada_scope):
    with aircanada_scope:
        h = fs.get_demand_history(pn="P-INT", location="YYZ-MAIN")
    assert h.n_observations() == 15


def test_open_order_qty_zero_for_unknown(fs, aircanada_scope):
    with aircanada_scope:
        assert fs.get_open_order_qty(pn="UNKNOWN", location="YYZ-MAIN") == 0


# (~30 more contract tests covering every observable behavior)
```

Both repos run this suite in CI. Drift between implementations causes both repos to fail.

### Task 25: Performance benchmark

Sub-10ms p99 for `get_features` against DynamoDB; sub-100ms p99 for `get_demand_history` against Iceberg with the right partition pruning.

### Tasks 26–27: Tenant-scoped read isolation, KMS encryption verification

---

## Phase 7: Lineage + SOC 2

### Task 28: OpenLineage emitters

Every Glue job emits OpenLineage events to a Marquez backend. Lineage graph traversable: `extract → manifest → ingest → derive → online → recommendation`.

### Task 29: Time-travel audit query

Given a recommendation's `provenance_id` and timestamp, reconstruct the *exact* features the model saw. Iceberg time-travel makes this a one-line query.

### Task 30: Audit S3 mirror

Every Iceberg manifest write also lands in the audit S3 bucket (Object Lock, Compliance mode, 7-yr retention). Cross-region replication for DR.

---

## Phase 8: Terraform per-tenant module + deploy

### Tasks 31–34

Per-tenant Terraform module wraps:
- KMS CMK + alias `trax-io/{tenant_id}`
- Landing S3 bucket prefix
- Lake S3 bucket
- Glue database + tables (created by Phase 1 init job)
- DynamoDB online table
- Kinesis event stream
- IAM roles (ingest, derive, online-update, event-cdc)
- EventBridge rule for landing-bucket triggers
- CloudWatch dashboards

CI runs `terraform plan` on every PR; `terraform apply` only on tagged releases via OIDC into the deployment account.

---

## Phase 9: Backfill + replay

### Tasks 35–37

Backfill tooling:
- **Historical bulk ingest** for new tenants — runs `ingest_nightly` over a date range, parallelized by partition.
- **Event replay** from audit bucket — re-run the event CDC job from any historical point.
- **Feature recompute** — invalidate derived tables and rebuild from raw Iceberg, used after a derived-feature schema change.

---

## Self-Review

| Spec section | Covered by |
|---|---|
| §4.1 Daily nightly path + Iceberg lake | Phases 1–2 |
| §4.1 Event path → Iceberg CDC | Phase 5 |
| §4.2 Offline features (Iceberg) + Online (DynamoDB) | Phases 3–4 |
| §4.2 Time-travel for audit reproducibility | Tasks 28–29 |
| §4.5 SOC 2 hooks | Tasks 28–30 + Terraform IAM least-privilege |
| §3.2 Tenant isolation (KMS, IAM, namespace) | Phase 8 Terraform per-tenant module |
| `FeatureStoreClient` Protocol implementation | Task 23 |
| Shared contract tests (per ADR-0002) | Task 24 |

**Estimated team:** 1 data-platform lead + 2 data engineers + 0.5 SRE = ~10 weeks elapsed.

**Critical-path note:** Phase 1 + Phase 2 (raw Iceberg + nightly ingest) are the gate for the entire platform. Sub-plan #4 cannot run real-data shadow mode until Phase 2 lands.
