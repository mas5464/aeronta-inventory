"""PySpark Glue job: pn_vendor_price (+ part_master costs)  -->  `vendor_economics` Iceberg table.

Sources:
- ``pn_vendor_price`` (#16) — one row per (PN, Vendor): unit price, MinOQ, preferred flag.
- ``part_master`` (#15) — part-level costs (market / average / repair), joined on PN.

Output keeps one row per (pn, vendor=HostVendorLocID) **and** synthesizes a canonical
``vendor="DEFAULT"`` row (the preferred, else cheapest, vendor) so the assembler resolves both
its open-order-vendor path and its no-open-order fallback (see reco assembler ``_resolve_vendor``).
``kit_cost`` is left null — the legacy ``getKitCost`` PL/SQL is not ported into v1.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from trax_io_feature_store.glue._common import (
    append_iceberg,
    coerce_int,
    disable_ansi_mode,
    load_manifest,
    read_artifacts,
    select_artifacts,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import DataFrame

LOG = logging.getLogger("trax_io.glue.vendor_economics")

VENDOR_ECONOMICS_COLUMNS: tuple[str, ...] = (
    "pn",
    "vendor",
    "unit_cost",
    "market_value_unit_cost",
    "average_cost",
    "kit_cost",
    "repair_cost_24mo_avg",
    "minimum_order_qty",
    "currency",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_PRICE_DOMAIN: frozenset[str] = frozenset({"pn_vendor_price"})
_PART_MASTER_DOMAIN: frozenset[str] = frozenset({"part_master"})
_ICEBERG_TABLE = "glue_catalog.trax_io.vendor_economics"
_CANONICAL_VENDOR = "DEFAULT"
_MONEY = "decimal(18,4)"
_TRUTHY = ("Y", "YES", "TRUE", "1")


def select_vendor_economics_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Price artifacts only (the required source); part_master is an optional enrichment."""
    return select_artifacts(manifest, _PRICE_DOMAIN)


def _part_master_costs(df: DataFrame | None):
    """PN -> (market/average/repair) costs, deduped. None when part_master is absent."""
    if df is None:
        return None
    from pyspark.sql import functions as F  # noqa: N812

    return (
        df.filter(F.col("HostPartID").isNotNull())
        .select(
            F.col("HostPartID").cast("string").alias("pn"),
            F.col("MarketUnitCost").cast(_MONEY).alias("market_value_unit_cost"),
            F.col("AverageCost").cast(_MONEY).alias("average_cost"),
            F.col("RepairCost").cast(_MONEY).alias("repair_cost_24mo_avg"),
        )
        .dropDuplicates(["pn"])
    )


def transform_to_vendor_economics(
    price_df: DataFrame,
    part_master_df: DataFrame | None,
    *,
    tenant_id: str,
    extract_date: date,
    manifest_sha256: str,
) -> DataFrame:
    from pyspark.sql import Window
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    priced = (
        price_df.filter(F.col("HostPartID").isNotNull() & F.col("HostVendorLocID").isNotNull())
        .select(
            F.col("HostPartID").cast("string").alias("pn"),
            F.col("HostVendorLocID").cast("string").alias("vendor"),
            F.coalesce(F.col("Price").cast(_MONEY), F.lit(0).cast(_MONEY)).alias("unit_cost"),
            # MinOQ floored to 1; coerce_int rounds (matches bridge ``max(1, _i(minoq, 1))``).
            F.greatest(coerce_int(F.col("MinOQ"), 1), F.lit(1)).alias("minimum_order_qty"),
            F.when(F.upper(F.col("Preferred")).isin(*_TRUTHY), F.lit(0))
            .otherwise(F.lit(1))
            .alias("_pref_rank"),
        )
        .dropDuplicates(["pn", "vendor"])
    )

    # Canonical DEFAULT row: preferred vendor first, then cheapest, then vendor id (deterministic).
    rank_w = Window.partitionBy("pn").orderBy(
        F.col("_pref_rank").asc(), F.col("unit_cost").asc(), F.col("vendor").asc()
    )
    canonical = (
        priced.withColumn("_rn", F.row_number().over(rank_w))
        .filter(F.col("_rn") == 1)
        .withColumn("vendor", F.lit(_CANONICAL_VENDOR))
        .drop("_rn")
    )

    # Reserve "DEFAULT" for the synthesized canonical: drop any real vendor that happens to be
    # literally named "DEFAULT" from the per-vendor arm so the union can't produce a
    # non-deterministic (pn, "DEFAULT") collision. (Real eMRO vendor-loc IDs are never "DEFAULT".)
    per_vendor = priced.filter(F.col("vendor") != F.lit(_CANONICAL_VENDOR))
    combined = per_vendor.unionByName(canonical).drop("_pref_rank")

    costs = _part_master_costs(part_master_df)
    if costs is not None:
        combined = combined.join(costs, on="pn", how="left")
    else:
        combined = (
            combined.withColumn("market_value_unit_cost", F.lit(None).cast(_MONEY))
            .withColumn("average_cost", F.lit(None).cast(_MONEY))
            .withColumn("repair_cost_24mo_avg", F.lit(None).cast(_MONEY))
        )

    ingested_at = datetime.now(UTC).replace(tzinfo=None)
    enriched = (
        combined.withColumn("kit_cost", F.lit(None).cast(_MONEY))
        .withColumn("currency", F.lit("USD"))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return enriched.dropDuplicates(["pn", "vendor"]).select(*VENDOR_ECONOMICS_COLUMNS)


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
    job.init(f"vendor-economics-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    price_artifacts = select_vendor_economics_artifacts(manifest)
    if not price_artifacts:
        LOG.warning("no succeeded pn_vendor_price artifact in manifest; nothing to do")
        job.commit()
        return

    pm_artifacts = select_artifacts(manifest, _PART_MASTER_DOMAIN)
    part_master_df = read_artifacts(spark, pm_artifacts) if pm_artifacts else None

    feature_df = transform_to_vendor_economics(
        read_artifacts(spark, price_artifacts),
        part_master_df,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    append_iceberg(feature_df, _ICEBERG_TABLE)
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
