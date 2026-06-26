"""PySpark Glue job: pn_vendor_price + order_plan_closed_orders  -->  `lead_time_distribution`.

Sources:
- ``pn_vendor_price`` (#16) — promised lead = the *preferred* vendor's ProcessingLength (0/missing
  -> 21.0), selected with the same preferred->cheapest->vendor ordering as vendor_economics.
- ``order_plan_closed_orders`` (#7) — realized lead days = ActualRcvDate - PlanOrderDate (>= 0).

Mirrors the reco bridge ``_lead_time`` exactly, including its **index-based** percentiles
(``realized[n//2]``, ``realized[min(n-1, round(0.9*(n-1)))]``) — NOT Spark's interpolating
``percentile_approx``. With no realized history: mean=p50=promised, p90=promised*1.3,
p99=promised*1.6, n=0. One row per PN present in pn_vendor_price; vendor="DEFAULT", condition="NEW".
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from trax_io_feature_store.glue._common import (
    append_iceberg,
    disable_ansi_mode,
    load_manifest,
    nonblank,
    parse_extract_date,
    read_artifacts,
    select_artifacts,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import DataFrame

LOG = logging.getLogger("trax_io.glue.lead_time_distribution")

LEAD_TIME_COLUMNS: tuple[str, ...] = (
    "pn",
    "vendor",
    "condition",
    "promised_lead_days",
    "realized_mean_days",
    "realized_p50_days",
    "realized_p90_days",
    "realized_p99_days",
    "promised_vs_actual_delta_mean",
    "n_observations",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_PRICE_DOMAIN: frozenset[str] = frozenset({"pn_vendor_price"})
_CLOSED_DOMAIN: frozenset[str] = frozenset({"order_plan_closed_orders"})
_ICEBERG_TABLE = "glue_catalog.trax_io.lead_time_distribution"
_CANONICAL_VENDOR = "DEFAULT"
_DEFAULT_PROMISED = 21.0
_TRUTHY = ("Y", "YES", "TRUE", "1")


def select_lead_time_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Price artifacts only (the row-driving source); closed orders are optional enrichment."""
    return select_artifacts(manifest, _PRICE_DOMAIN)


def _promised_per_pn(price_df: DataFrame) -> DataFrame:
    """Preferred-vendor ProcessingLength per pn (0/missing -> 21.0), one row per pn."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    priced = price_df.filter(nonblank(F.col("HostPartID"))).select(
        F.col("HostPartID").cast(T.StringType()).alias("pn"),
        F.col("HostVendorLocID").cast(T.StringType()).alias("vendor"),
        F.coalesce(F.col("Price").cast(T.DoubleType()), F.lit(0.0)).alias("price_sort"),
        F.col("ProcessingLength").cast(T.DoubleType()).alias("processing"),
        F.when(F.upper(F.col("Preferred")).isin(*_TRUTHY), F.lit(0)).otherwise(F.lit(1)).alias(
            "pref_rank"
        ),
    )
    # Deterministic preferred-vendor pick (same ordering as vendor_economics' DEFAULT row). The
    # bridge's stable sort keys on the preferred flag only and keeps JSON input order among ties,
    # which Spark cannot reproduce; the price/vendor secondary keys are an accepted, bounded
    # difference (only matters when same-preferred rows disagree on ProcessingLength).
    w = Window.partitionBy("pn").orderBy(
        F.col("pref_rank").asc(), F.col("price_sort").asc(), F.col("vendor").asc()
    )
    return (
        priced.withColumn("_rn", F.row_number().over(w))
        .filter(F.col("_rn") == 1)
        .withColumn(
            "promised_lead_days",
            F.when(
                F.col("processing").isNull() | (F.col("processing") == F.lit(0.0)),
                F.lit(_DEFAULT_PROMISED),
            ).otherwise(F.col("processing")),
        )
        .select("pn", "promised_lead_days")
    )


def _realized_per_pn(closed_df: DataFrame | None):
    """Sorted realized lead-day array + count + mean per pn, or None when no closed-orders."""
    if closed_df is None:
        return None
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    closed = (
        closed_df.select(
            F.col("HostPartID").cast(T.StringType()).alias("pn"),
            parse_extract_date(F.col("PlanOrderDate")).alias("ordered"),
            parse_extract_date(F.col("ActualRcvDate")).alias("received"),
        )
        .filter(
            nonblank(F.col("pn"))
            & F.col("ordered").isNotNull()
            & F.col("received").isNotNull()
            & (F.col("received") >= F.col("ordered"))
        )
        .withColumn("days", F.datediff(F.col("received"), F.col("ordered")))
    )
    return closed.groupBy("pn").agg(
        F.sort_array(F.collect_list("days")).alias("sorted_days"),
        F.count(F.lit(1)).alias("n"),
        F.avg("days").alias("mean_realized"),
    )


def transform_to_lead_time(
    price_df: DataFrame,
    closed_df: DataFrame | None,
    *,
    tenant_id: str,
    extract_date: date,
    manifest_sha256: str,
) -> DataFrame:
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    promised = _promised_per_pn(price_df)
    realized = _realized_per_pn(closed_df)
    joined = promised.join(realized, on="pn", how="left") if realized is not None else (
        promised.withColumn("sorted_days", F.lit(None).cast(T.ArrayType(T.IntegerType())))
        .withColumn("n", F.lit(None).cast(T.LongType()))
        .withColumn("mean_realized", F.lit(None).cast(T.DoubleType()))
    )

    n = F.col("n")
    has = n.isNotNull() & (n > F.lit(0))
    sd = F.col("sorted_days")
    p = F.col("promised_lead_days")
    # 0-based indices from the bridge, +1 for Spark's 1-based element_at (must be INT, not BIGINT).
    n1 = n - F.lit(1)
    idx50 = (F.floor(n / F.lit(2)) + F.lit(1)).cast(T.IntegerType())
    idx90 = (
        F.least(n1, F.bround(F.lit(0.9) * n1).cast(T.IntegerType())) + F.lit(1)
    ).cast(T.IntegerType())
    idx99 = (
        F.least(n1, F.bround(F.lit(0.99) * n1).cast(T.IntegerType())) + F.lit(1)
    ).cast(T.IntegerType())
    p50 = F.element_at(sd, idx50).cast(T.DoubleType())
    p90 = F.element_at(sd, idx90).cast(T.DoubleType())
    p99 = F.element_at(sd, idx99).cast(T.DoubleType())

    mean_days = F.when(has, F.col("mean_realized")).otherwise(p)
    p50_days = F.when(has, p50).otherwise(p)
    p90_days = F.when(has, p90).otherwise(p * F.lit(1.3))
    p99_days = F.when(has, p99).otherwise(p * F.lit(1.6))

    ingested_at = datetime.now(UTC).replace(tzinfo=None)
    out = (
        joined.withColumn("vendor", F.lit(_CANONICAL_VENDOR))
        .withColumn("condition", F.lit("NEW"))
        .withColumn("realized_mean_days", mean_days)
        .withColumn("realized_p50_days", p50_days)
        .withColumn("realized_p90_days", p90_days)
        .withColumn("realized_p99_days", p99_days)
        .withColumn("promised_vs_actual_delta_mean", mean_days - p)
        .withColumn("n_observations", F.coalesce(n, F.lit(0)).cast(T.IntegerType()))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return out.select(*LEAD_TIME_COLUMNS)


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
    job.init(f"lead-time-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    price_artifacts = select_lead_time_artifacts(manifest)
    if not price_artifacts:
        LOG.warning("no succeeded pn_vendor_price artifact in manifest; nothing to do")
        job.commit()
        return

    closed_artifacts = select_artifacts(manifest, _CLOSED_DOMAIN)
    closed_df = read_artifacts(spark, closed_artifacts) if closed_artifacts else None

    feature_df = transform_to_lead_time(
        read_artifacts(spark, price_artifacts),
        closed_df,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    append_iceberg(feature_df, _ICEBERG_TABLE)
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
