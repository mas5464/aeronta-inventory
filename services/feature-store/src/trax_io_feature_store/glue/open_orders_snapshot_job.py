"""PySpark Glue job: raw order_plan JSON  -->  `open_orders_snapshot` Iceberg table.

Source: extract domain ``order_plan`` (#8). Purchase orders remain eligible only
while truly OPEN; repair orders retain every reported lifecycle state so
identity, age, terminal, and ineligible-status evidence reaches conservative
reconciliation.
``qty_open = max(0, PlanQuantity - ReceivedQuantity)`` and rows with no open
quantity are dropped. Rows are grouped to one snapshot per (pn, location) with
the per-order structs sorted for deterministic output (SOC 2 reproducibility).
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, time, timezone
from typing import TYPE_CHECKING, Any

from trax_io_feature_store.glue._common import (
    append_feature_group,
    coerce_int,
    disable_ansi_mode,
    finite_double,
    iceberg_table_identifier,
    load_manifest,
    nonblank,
    normalize_planning_keys,
    parse_extract_date,
    read_artifacts_with_schema,
    read_planning_key_artifacts,
    select_artifacts,
    validate_manifest_identity,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import DataFrame

LOG = logging.getLogger("trax_io.glue.open_orders_snapshot")

OPEN_ORDERS_COLUMNS: tuple[str, ...] = (
    "pn",
    "location",
    "snapshot_at",
    "orders",
    "total_open_qty",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_DOMAIN: frozenset[str] = frozenset({"order_plan"})
_PLANNING_KEY_DOMAINS: frozenset[str] = frozenset(
    {"stock_amount", "stock_level_upload"}
)
_ICEBERG_TABLE = "glue_catalog.trax_io.open_orders_snapshot"


def select_open_orders_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _DOMAIN)


def select_planning_key_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _PLANNING_KEY_DOMAINS)


def read_open_orders_artifacts(
    spark: Any,
    artifacts: list[dict[str, Any]],
) -> DataFrame:
    """Read order_plan with a strict schema that preserves a valid empty file."""

    from pyspark.sql import types as T  # noqa: N812

    schema = T.StructType(
        [
            T.StructField("HostPartID", T.StringType(), True),
            T.StructField("HostLocID", T.StringType(), True),
            T.StructField("OrderStatus", T.StringType(), True),
            T.StructField("OrderTypeID", T.StringType(), True),
            T.StructField("HostOrderID", T.StringType(), True),
            T.StructField("OrderID", T.StringType(), True),
            T.StructField("OrderLineID", T.StringType(), True),
            T.StructField("HostVendorLocID", T.StringType(), True),
            T.StructField("HostShopID", T.StringType(), True),
            T.StructField("PlanOrderDate", T.StringType(), True),
            T.StructField("SerialNumber", T.StringType(), True),
            T.StructField("PlanQuantity", T.StringType(), True),
            T.StructField("ReceivedQuantity", T.StringType(), True),
            T.StructField("PlanRcvDate", T.StringType(), True),
        ]
    )
    return read_artifacts_with_schema(spark, artifacts, schema)


def transform_to_open_orders(
    df: DataFrame,
    *,
    tenant_id: str,
    extract_date: date,
    manifest_sha256: str,
    planning_keys: DataFrame | None = None,
    planning_active_only: bool = False,
) -> DataFrame:
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    source_columns = {column.lower(): column for column in df.columns}

    def source(column: str) -> Any:
        actual = source_columns.get(column.lower())
        return F.col(actual) if actual is not None else F.lit(None)

    def optional_string(column: str) -> Any:
        raw = F.trim(source(column).cast(T.StringType()))
        return F.when(raw.isNull() | (raw == F.lit("")), F.lit(None)).otherwise(raw)

    def optional_timestamp(column: str) -> Any:
        raw = source(column).cast(T.StringType())
        return F.coalesce(
            F.to_timestamp(raw, "MM/dd/yyyy HH:mm:ss"),
            F.to_timestamp(raw, "MM/dd/yyyy HH:mm"),
            F.to_timestamp(raw, "MM/dd/yyyy"),
            F.to_timestamp(raw),
        )

    plan_quantity = F.col("PlanQuantity").cast(T.DoubleType())
    received_quantity = F.col("ReceivedQuantity").cast(T.DoubleType())
    expected_raw = F.col("PlanRcvDate").cast(T.StringType())
    expected_blank = expected_raw.isNull() | (F.trim(expected_raw) == F.lit(""))
    valid = (
        nonblank(F.col("HostPartID"))
        & nonblank(F.col("HostLocID"))
        & nonblank(F.col("OrderStatus"))
        & nonblank(F.col("PlanQuantity"))
        & nonblank(F.col("ReceivedQuantity"))
        & finite_double(plan_quantity)
        & finite_double(received_quantity)
        & (expected_blank | parse_extract_date(expected_raw).isNotNull())
    )
    invalid_count = df.filter(~valid).count()
    if invalid_count:
        raise ValueError(
            "order_plan artifact contains "
            f"{invalid_count} row(s) with invalid required fields"
        )

    pn = F.col("HostPartID").cast(T.StringType())
    location = F.col("HostLocID").cast(T.StringType())
    status_raw = F.trim(
        F.coalesce(source("OrderStatus").cast(T.StringType()), F.lit(""))
    )
    status = F.when(status_raw == F.lit(""), F.lit("OPEN")).otherwise(
        F.upper(status_raw)
    )
    explicit_type = F.upper(
        F.trim(
            F.coalesce(
                source("OrderTypeID").cast(T.StringType()),
                F.lit(""),
            )
        )
    )
    host_order_id = F.upper(
        F.trim(
            F.coalesce(
                source("HostOrderID").cast(T.StringType()),
                F.lit(""),
            )
        )
    )
    legacy_order_id = F.upper(
        F.trim(
            F.coalesce(
                source("OrderID").cast(T.StringType()),
                F.lit(""),
            )
        )
    )
    po_prefix = host_order_id.rlike(r"^PO(?:_|-|/)") | legacy_order_id.rlike(
        r"^PO(?:_|-|/)"
    )
    ro_prefix = host_order_id.rlike(r"^RO(?:_|-|/)") | legacy_order_id.rlike(
        r"^RO(?:_|-|/)"
    )
    legacy_type = (
        F.when(po_prefix & ~ro_prefix, F.lit("PO"))
        .when(ro_prefix & ~po_prefix, F.lit("RO"))
        .otherwise(F.lit(None).cast(T.StringType()))
    )
    order_type = (
        F.when(explicit_type.isin("PO", "RO"), explicit_type)
        .when(explicit_type != F.lit(""), F.lit(None).cast(T.StringType()))
        .otherwise(legacy_type)
    )
    # Procurement contributes supply only while OPEN. Repair rows retain every
    # lifecycle state with remaining source quantity so the repair pipeline can
    # disclose terminal/ineligible exclusions instead of silently erasing them.
    eligible_status = (order_type == F.lit("RO")) | (
        (order_type == F.lit("PO")) & (status == F.lit("OPEN"))
    )
    qty_open = F.greatest(
        F.lit(0),
        coerce_int(F.col("PlanQuantity"), 0)
        - coerce_int(F.col("ReceivedQuantity"), 0),
    )
    unclassified_count = df.filter(
        nonblank(F.col("HostPartID"))
        & nonblank(F.col("HostLocID"))
        & (qty_open > 0)
        & order_type.isNull()
    ).count()
    if unclassified_count:
        LOG.warning(
            "excluded %d open-order row(s) with unclassified order type",
            unclassified_count,
        )
    eligible = df.filter(
        nonblank(F.col("HostPartID"))
        & nonblank(F.col("HostLocID"))
        & order_type.isNotNull()
        & eligible_status
    )
    order_id_raw = F.trim(F.coalesce(F.col("HostOrderID").cast(T.StringType()), F.lit("")))
    per_row = (
        eligible.withColumn("pn", pn)
        .withColumn("location", location)
        .withColumn("qty_open", qty_open)
        .filter(F.col("qty_open") > 0)
        .withColumn(
            "order",
            F.struct(
                F.when(order_id_raw == "", F.lit("?")).otherwise(order_id_raw).alias("order_id"),
                order_type.alias("order_type"),
                optional_string("HostVendorLocID").alias("vendor"),
                F.col("qty_open").alias("qty_open"),
                parse_extract_date(F.col("PlanRcvDate")).alias("expected_rcv_date"),
                optional_string("OrderLineID").alias("order_line_id"),
                optional_timestamp("PlanOrderDate").alias("opened_at"),
                status.alias("status"),
                optional_string("SerialNumber").alias("serial_number"),
                optional_string("HostShopID").alias("shop"),
                F.col("location").alias("location"),
            ),
        )
    )

    grouped = (
        per_row.groupBy("pn", "location")
        .agg(
            F.sort_array(F.collect_list("order")).alias("orders"),
            F.sum("qty_open").cast(T.IntegerType()).alias("total_open_qty"),
        )
    )
    if planning_keys is not None:
        stock_keys = normalize_planning_keys(
            planning_keys,
            planning_active_only=planning_active_only,
        )
        existing = grouped.select("pn", "location")
        empty_snapshots = (
            stock_keys.join(existing, ["pn", "location"], "left_anti")
            .withColumn(
                "orders",
                F.array().cast(grouped.schema["orders"].dataType),
            )
            .withColumn("total_open_qty", F.lit(0).cast(T.IntegerType()))
        )
        grouped = grouped.unionByName(empty_snapshots)

    snapshot_at = datetime.combine(extract_date, time())
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    enriched = (
        grouped
        .withColumn("snapshot_at", F.lit(snapshot_at).cast(T.TimestampType()))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return enriched.select(*OPEN_ORDERS_COLUMNS)


def _parse_args(argv: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    it = iter(argv)
    for tok in it:
        if tok.startswith("--"):
            try:
                out[tok[2:]] = next(it)
            except StopIteration as exc:
                raise ValueError(f"missing value for arg {tok!r}") from exc
    missing = {"tenant_id", "extract_date", "lake_bucket", "manifest_s3_uri"} - out.keys()
    if missing:
        raise ValueError(f"missing required args: {sorted(missing)}")
    return out


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    from awsglue.context import GlueContext  # type: ignore[import-not-found]
    from awsglue.job import Job  # type: ignore[import-not-found]
    from pyspark.context import SparkContext

    sc = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    disable_ansi_mode(spark)
    job = Job(glue_ctx)
    job.init(f"open-orders-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    validate_manifest_identity(
        manifest,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
    )
    artifacts = select_open_orders_artifacts(manifest)
    if not artifacts:
        LOG.warning("no succeeded order_plan artifact in manifest; nothing to do")
        job.commit()
        return

    planning_key_artifacts = select_planning_key_artifacts(manifest)
    planning_keys = (
        read_planning_key_artifacts(spark, planning_key_artifacts)
        if planning_key_artifacts
        else None
    )
    feature_df = transform_to_open_orders(
        read_open_orders_artifacts(spark, artifacts),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
        planning_keys=planning_keys,
        planning_active_only=bool(
            manifest.get("pool_by_part")
            or manifest.get("scope_mode")
            in {
                "planning_active",
                "network_planning_active",
            }
        ),
    )
    append_feature_group(
        feature_df,
        target_table=iceberg_table_identifier(
            args,
            "open_orders_snapshot",
        ),
        status_table=iceberg_table_identifier(args, "feature_batch_status"),
        feature_group="open_orders_snapshot",
        run_id=str(manifest.get("run_id") or ""),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
