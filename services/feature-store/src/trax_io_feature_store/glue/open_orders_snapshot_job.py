"""PySpark Glue job: raw order_plan JSON  -->  `open_orders_snapshot` Iceberg table.

Source: extract domain ``order_plan`` (#8), filtered to OPEN orders. Mirrors the reco bridge:
``qty_open = max(0, PlanQuantity - ReceivedQuantity)`` (rows with no open qty dropped),
``order_type`` RO vs PO, vendor null. Rows are grouped to one snapshot per (pn, location) with
the per-order structs sorted for deterministic output (SOC 2 reproducibility).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

from trax_io_feature_store.glue._common import (
    append_iceberg,
    coerce_int,
    disable_ansi_mode,
    load_manifest,
    nonblank,
    parse_extract_date,
    read_artifacts,
    select_artifacts,
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
_ICEBERG_TABLE = "glue_catalog.trax_io.open_orders_snapshot"


def select_open_orders_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _DOMAIN)


def transform_to_open_orders(
    df: DataFrame, *, tenant_id: str, extract_date: date, manifest_sha256: str
) -> DataFrame:
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    pn = F.col("HostPartID").cast(T.StringType())
    location = F.col("HostLocID").cast(T.StringType())
    open_only = df.filter(
        nonblank(F.col("HostPartID"))
        & nonblank(F.col("HostLocID"))
        & (F.upper(F.col("OrderStatus")) == F.lit("OPEN"))
    )
    qty_open = F.greatest(
        F.lit(0), coerce_int(F.col("PlanQuantity"), 0) - coerce_int(F.col("ReceivedQuantity"), 0)
    )
    order_id_raw = F.trim(F.coalesce(F.col("HostOrderID").cast(T.StringType()), F.lit("")))
    per_row = (
        open_only.withColumn("pn", pn)
        .withColumn("location", location)
        .withColumn("qty_open", qty_open)
        .filter(F.col("qty_open") > 0)
        .withColumn(
            "order",
            F.struct(
                F.when(order_id_raw == "", F.lit("?")).otherwise(order_id_raw).alias("order_id"),
                F.when(F.upper(F.col("OrderTypeID")) == F.lit("RO"), F.lit("RO"))
                .otherwise(F.lit("PO"))
                .alias("order_type"),
                F.lit(None).cast(T.StringType()).alias("vendor"),
                F.col("qty_open").alias("qty_open"),
                parse_extract_date(F.col("PlanRcvDate")).alias("expected_rcv_date"),
            ),
        )
    )

    snapshot_at = datetime.combine(extract_date, time())
    ingested_at = datetime.now(UTC).replace(tzinfo=None)
    grouped = (
        per_row.groupBy("pn", "location")
        .agg(
            F.sort_array(F.collect_list("order")).alias("orders"),
            F.sum("qty_open").cast(T.IntegerType()).alias("total_open_qty"),
        )
        .withColumn("snapshot_at", F.lit(snapshot_at).cast(T.TimestampType()))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return grouped.select(*OPEN_ORDERS_COLUMNS)


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
    artifacts = select_open_orders_artifacts(manifest)
    if not artifacts:
        LOG.warning("no succeeded order_plan artifact in manifest; nothing to do")
        job.commit()
        return

    feature_df = transform_to_open_orders(
        read_artifacts(spark, artifacts),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    append_iceberg(feature_df, _ICEBERG_TABLE)
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
