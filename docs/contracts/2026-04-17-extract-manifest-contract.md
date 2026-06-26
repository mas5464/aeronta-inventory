# ExtractManifest Contract — Nightly Extract ↔ Feature Store

**Date:** 2026-04-17
**Owner:** Miguel Sosa
**Status:** Draft v1
**Source SQL:** `/Users/miguelsosa/Downloads/PTC Project Files/eMRO Data SQLs.sql`

---

## Purpose

The `ExtractManifest` is the handshake between sub-project **#1 Nightly Extract Utility** (producer) and sub-project **#2 Feature Store & Data Lake** (consumer). After each nightly run, #1 writes one manifest describing exactly what landed in S3; #2's Glue ingestion reads it to know which files to pick up, their integrity, and per-domain success/failure.

## Scope — 21 canonical extract domains

Pinned from the customer's canonical SQL file. These are the **raw landing datasets** (bronze layer), not the 10 derived feature groups.

| # | Domain | Date-windowed | Primary eMRO tables |
|---|---|---|---|
| 1 | `causal_values` | ✅ `:start_date`, `:end_date` | AC_ACTUAL_FLIGHTS, AC_MASTER |
| 2 | `demand_history_rotables` | ✅ `:from_date`, `:to_date` | AC_PN_TRANSACTION_HISTORY, WO, DEFECT_REPORT |
| 3 | `demand_history_expendables` | ✅ `:from_date`, `:to_date` | PN_INVENTORY_HISTORY, DEFECT_REPORT(_PN) |
| 4 | `events` | ✅ `:as_of_date`, `:transaction` | PLANNING, WO, WO_ENGINEERING_ORDER |
| 5 | `location_master` | snapshot | LOCATION_MASTER |
| 6 | `location_type` | snapshot | SYSTEM_TRAN_CODE |
| 7 | `order_plan_closed_orders` | snapshot | ORDER_DETAIL, ORDER_HEADER, PN_INVENTORY_HISTORY |
| 8 | `order_plan` | snapshot | ORDER_HEADER, ORDER_DETAIL, PN_INVENTORY_HISTORY |
| 9 | `order_plan_data_requisition` | snapshot | REQUISITION_HEADER, REQUISITION_DETAIL, ORDER_DETAIL |
| 10 | `part_chain` | snapshot | PN_INTERCHANGEABLE, NOTE_PAD |
| 11 | `part_chain_details` | snapshot | PN_INTERCHANGEABLE, PN_INTERCHG_ONE_WAY, NOTE_PAD |
| 12 | `part_criticality` | snapshot | SYSTEM_TRAN_CODE (ESSENTIALITY) |
| 13 | `part_kit_bom` | snapshot | PN_NEXT_LOWER_ASSEMBLY |
| 14 | `part_location` | snapshot | PN_INTERCHANGEABLE, PN_MASTER, PN_INVENTORY_LEVEL, LOCATION_MASTER, PN_VENDOR_PRICE |
| 15 | `part_master` | snapshot | PN_INTERCHANGEABLE, PN_MASTER, PN_EFFECTIVITY_HEADER, PN_INVENTORY_HISTORY, ORDER_INVOICE |
| 16 | `pn_vendor_price` | snapshot | PN_VENDOR_PRICE |
| 17 | `sales_order` | snapshot | CUSTOMER_ORDER_DETAIL, CUSTOMER_ORDER_HEADER |
| 18 | `stock_amount` | snapshot | PN_INVENTORY_DETAIL, LOCATION_MASTER |
| 19 | `stock_level_upload` | snapshot | PN_INVENTORY_LEVEL |
| 20 | `trans_code` | snapshot | SYSTEM_TRAN_CODE (PNCATEGORY) |
| 21 | `vendor` | snapshot | RELATION_MASTER, NOTE_PAD |

## Bind variable contract

The canonical SQL file uses string-literal placeholders (`' startDate '`, `' fromDate '`, `' toDate '`, `' date '`, `' transaction '`). These are **unsafe** and must be replaced with Oracle bind variables before production use:

| Placeholder (in source) | Bind variable | Bound from |
|---|---|---|
| `' startDate '` | `:start_date` | `--extract-date` minus window |
| `' endDate   '` / `'endDate'` | `:end_date` | `--extract-date` |
| `' fromDate '` | `:from_date` | `--extract-date` minus window |
| `'  toDate '` | `:to_date` | `--extract-date` |
| `' date '` | `:as_of_date` | `--extract-date` |
| `' transaction '` | `:transaction` | CLI flag (domain #4 `events` only) |

Date format in the source SQL is `mm/dd/yyyy` (with `HH24:MI` for transaction history). Bind variables are passed as Python `datetime.date` / `datetime`; the driver handles formatting.

## Atomicity — partial success allowed

Per product decision 2026-04-17: a nightly extract that partially fails (e.g., 20/21 domains succeed) emits a manifest with the failed domain marked `status: failed`. #2's Glue job ingests the 20 successes and alerts on the failure. Rationale: planners prefer 20 clean datasets over a full-day blackout.

Hard exception: if **all** date-windowed domains (1–4) fail, the manifest is flagged `run_status: degraded` and #2 holds ingestion pending operator review.

## ExtractManifest schema (v1.0.0)

```python
# pydantic v2 — lives in tools/nightly-extract/src/trax_io_extract/manifest.py
# and is re-exported for #2's Glue ingest job.

from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt


class DomainArtifact(BaseModel):
    """One raw landing artifact for one of the 21 domains."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str                    # e.g., "causal_values"
    status: Literal["succeeded", "failed", "skipped"]
    s3_uri: str | None             # None when status != "succeeded"
    row_count: NonNegativeInt = 0
    sha256: str | None = None      # hex digest of the file contents
    bytes: NonNegativeInt = 0
    bind_vars: dict[str, str] = Field(default_factory=dict)  # serialized
    started_at: datetime
    finished_at: datetime
    error_code: str | None = None  # ORA-xxxxx when failed
    error_message: str | None = None


class ExtractManifest(BaseModel):
    """Emitted once per nightly extract run; landed as manifest.json next to
    the 21 domain artifacts.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0.0"] = "1.0.0"
    tenant_id: str                         # lighthouse tenant identifier
    extract_date: date                     # logical nightly date (UTC)
    run_id: str                            # ULID — globally unique per run
    run_status: Literal["succeeded", "partial", "degraded", "failed"]
    started_at: datetime
    finished_at: datetime
    source: Literal["eMRO-Oracle"] = "eMRO-Oracle"
    source_sql_sha256: str                 # hash of the bundled SQL pack
    extract_utility_version: str           # semver of trax-io-extract
    artifacts: list[DomainArtifact]        # exactly 21 entries (one per domain)
```

## Landing layout

```
s3://trax-io-<tenant>-<env>-landing/
  extract_date=2026-04-17/
    run_id=01JS7W2F.../
      manifest.json
      causal_values.json
      demand_history_rotables.json
      demand_history_expendables.json
      events.json
      location_master.json
      ... (21 total)
```

Partitioning by `extract_date` makes Glue's partition discovery trivial. `run_id` is ULID so it sorts; if a nightly is re-run, a second run_id appears under the same date and `#2` picks the latest.

## Consumer responsibilities (#2)

1. Read `manifest.json`; verify `schema_version == "1.0.0"`.
2. For each `DomainArtifact` with `status == "succeeded"`: download, verify `sha256`, verify `row_count`, transform into the relevant feature group(s).
3. Skip artifacts with `status in {"failed", "skipped"}` and emit an observability event (`extract.partial_domain_missing`).
4. If `run_status == "degraded"`: halt ingestion, page platform on-call.

## Open questions for next session

- **Oracle package dependency:** SQLs reference `PKG_TRAX_PTC.getKitCost`, `PKG_TRAX_PTC.getRecordsType`, `pkg_settings_pn_master.getPNCategory`. Confirm these packages are deployed on each customer eMRO DB — or inline the logic in the extract queries.
- **Default date windows:** per-domain defaults (e.g., `causal_values` = 90 days, `demand_history_*` = 36 months). Needs product sign-off before Phase 2.
- **File format:** Phase 1 spec above uses JSON. For Phase 2 production, consider gzip-JSON or Parquet — JSON is human-debuggable but ~3-5× larger. Deferred.
- **Per-tenant KMS encryption:** S3 landing bucket writes must use the tenant's CMK (from #9). Manifest file itself must also be encrypted. Wire when #9 exports the per-tenant key ARN.
