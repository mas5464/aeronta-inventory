"""PySpark Glue job: raw requisition order-plan JSON -> requisition_snapshot.

The requisition feed represents demand, not inbound supply. A succeeded feed
materializes one snapshot for every planning key, including explicit empty
snapshots. A failed or absent artifact is not read; run-status coherence in the
Iceberg reader prevents an older snapshot from being served as current.
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

LOG = logging.getLogger("trax_io.glue.requisition_snapshot")

REQUISITION_COLUMNS: tuple[str, ...] = (
    "pn",
    "location",
    "snapshot_at",
    "lines",
    "total_qty_needed",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_DOMAIN: frozenset[str] = frozenset({"order_plan_data_requisition"})
_PLANNING_KEY_DOMAINS: frozenset[str] = frozenset(
    {"stock_amount", "stock_level_upload"}
)
_ICEBERG_TABLE = "glue_catalog.trax_io.requisition_snapshot"


def select_requisition_artifacts(
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _DOMAIN)


def select_planning_key_artifacts(
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _PLANNING_KEY_DOMAINS)


def read_requisition_artifacts(
    spark: Any,
    artifacts: list[dict[str, Any]],
) -> DataFrame:
    """Strict reader whose explicit schema preserves valid empty artifacts."""

    from pyspark.sql import types as T  # noqa: N812

    schema = T.StructType(
        [
            T.StructField("HostPartID", T.StringType(), True),
            T.StructField("HostLocID", T.StringType(), True),
            T.StructField("HostOrderID", T.StringType(), True),
            T.StructField("OrderStatus", T.StringType(), True),
            T.StructField("PlanQuantity", T.StringType(), True),
            T.StructField("ReceivedQuantity", T.StringType(), True),
            T.StructField("PlanRcvDate", T.StringType(), True),
            T.StructField("HostReplSourceLocID", T.StringType(), True),
        ]
    )
    return read_artifacts_with_schema(spark, artifacts, schema)


def transform_to_requisitions(
    df: DataFrame,
    *,
    tenant_id: str,
    extract_date: date,
    manifest_sha256: str,
    planning_keys: DataFrame | None = None,
    planning_active_only: bool = False,
) -> DataFrame:
    """Aggregate open requisition lines and emit observed-empty key markers."""

    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    plan_quantity = F.col("PlanQuantity").cast(T.DoubleType())
    received_quantity = F.col("ReceivedQuantity").cast(T.DoubleType())
    need_by_raw = F.col("PlanRcvDate").cast(T.StringType())
    need_by_blank = need_by_raw.isNull() | (
        F.trim(need_by_raw) == F.lit("")
    )
    valid = (
        nonblank(F.col("HostPartID"))
        & nonblank(F.col("HostLocID"))
        & nonblank(F.col("HostOrderID"))
        & nonblank(F.col("OrderStatus"))
        & nonblank(F.col("PlanQuantity"))
        & nonblank(F.col("ReceivedQuantity"))
        & finite_double(plan_quantity)
        & finite_double(received_quantity)
        & (need_by_blank | parse_extract_date(need_by_raw).isNotNull())
    )
    invalid_count = df.filter(~valid).count()
    if invalid_count:
        raise ValueError(
            "requisition artifact contains "
            f"{invalid_count} row(s) with invalid required fields"
        )

    open_only = df.filter(
        nonblank(F.col("HostPartID"))
        & nonblank(F.col("HostLocID"))
        & (F.upper(F.col("OrderStatus")) == F.lit("OPEN"))
    )
    qty_needed = F.greatest(
        F.lit(0),
        coerce_int(F.col("PlanQuantity"), 0)
        - coerce_int(F.col("ReceivedQuantity"), 0),
    )
    requisition_id = F.trim(
        F.coalesce(F.col("HostOrderID").cast(T.StringType()), F.lit(""))
    )
    alt_source = F.col("HostReplSourceLocID").cast(T.StringType())
    per_row = (
        open_only.withColumn(
            "pn",
            F.col("HostPartID").cast(T.StringType()),
        )
        .withColumn(
            "location",
            F.col("HostLocID").cast(T.StringType()),
        )
        .withColumn("qty_needed", qty_needed)
        .filter(F.col("qty_needed") > 0)
        .withColumn(
            "line",
            F.struct(
                F.when(requisition_id == "", F.lit("?"))
                .otherwise(requisition_id)
                .alias("requisition_id"),
                F.col("qty_needed").alias("qty_needed"),
                parse_extract_date(F.col("PlanRcvDate")).alias("need_by"),
                F.when(nonblank(alt_source), F.trim(alt_source))
                .otherwise(F.lit(None).cast(T.StringType()))
                .alias("alt_source_location"),
            ),
        )
    )

    grouped = per_row.groupBy("pn", "location").agg(
        F.sort_array(F.collect_list("line")).alias("lines"),
        F.sum("qty_needed").cast(T.IntegerType()).alias("total_qty_needed"),
    )
    if planning_keys is not None:
        stock_keys = normalize_planning_keys(
            planning_keys,
            planning_active_only=planning_active_only,
        )
        empty_snapshots = (
            stock_keys.join(
                grouped.select("pn", "location"),
                ["pn", "location"],
                "left_anti",
            )
            .withColumn(
                "lines",
                F.array().cast(grouped.schema["lines"].dataType),
            )
            .withColumn("total_qty_needed", F.lit(0).cast(T.IntegerType()))
        )
        grouped = grouped.unionByName(empty_snapshots)

    snapshot_at = datetime.combine(extract_date, time())
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    enriched = (
        grouped.withColumn(
            "snapshot_at",
            F.lit(snapshot_at).cast(T.TimestampType()),
        )
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn(
            "ingested_at",
            F.lit(ingested_at).cast(T.TimestampType()),
        )
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn(
            "extract_date",
            F.lit(extract_date).cast(T.DateType()),
        )
    )
    return enriched.select(*REQUISITION_COLUMNS)


def _parse_args(argv: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    iterator = iter(argv)
    for token in iterator:
        if token.startswith("--"):
            try:
                out[token[2:]] = next(iterator)
            except StopIteration as exc:
                raise ValueError(f"missing value for arg {token!r}") from exc
    missing = {
        "tenant_id",
        "extract_date",
        "lake_bucket",
        "manifest_s3_uri",
    } - out.keys()
    if missing:
        raise ValueError(f"missing required args: {sorted(missing)}")
    return out


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    from awsglue.context import GlueContext  # type: ignore[import-not-found]
    from awsglue.job import Job  # type: ignore[import-not-found]
    from pyspark.context import SparkContext

    spark_context = SparkContext.getOrCreate()
    glue_context = GlueContext(spark_context)
    spark = glue_context.spark_session
    disable_ansi_mode(spark)
    job = Job(glue_context)
    job.init(
        f"requisition-snapshot-{args['tenant_id']}-{args['extract_date']}",
        args,
    )

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    validate_manifest_identity(
        manifest,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
    )
    artifacts = select_requisition_artifacts(manifest)
    if not artifacts:
        LOG.warning(
            "no succeeded order_plan_data_requisition artifact; nothing to do"
        )
        job.commit()
        return

    planning_artifacts = select_planning_key_artifacts(manifest)
    planning_keys = (
        read_planning_key_artifacts(spark, planning_artifacts)
        if planning_artifacts
        else None
    )
    feature_df = transform_to_requisitions(
        read_requisition_artifacts(spark, artifacts),
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
            "requisition_snapshot",
        ),
        status_table=iceberg_table_identifier(args, "feature_batch_status"),
        feature_group="requisition_snapshot",
        run_id=str(manifest.get("run_id") or ""),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
