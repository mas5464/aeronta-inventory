"""PySpark Glue job: raw location_master JSON  -->  `location_graph` Iceberg table.

Source: extract domain ``location_master`` (#5), one row per location. Mirrors the reco bridge:
``related_main_warehouse`` is the parent (empty -> null); ``role`` is "outstation" when the
parent is set and differs from the location, else "main". ``children`` is left an empty array
to match the bridge (child-rollup is a deferred enhancement in both pipelines).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from trax_io_feature_store.glue._common import (
    append_iceberg,
    dedupe_first,
    disable_ansi_mode,
    load_manifest,
    nonblank,
    read_artifacts,
    select_artifacts,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import DataFrame

LOG = logging.getLogger("trax_io.glue.location_graph")

LOCATION_GRAPH_COLUMNS: tuple[str, ...] = (
    "location",
    "related_main_warehouse",
    "role",
    "children",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_DOMAIN: frozenset[str] = frozenset({"location_master"})
_ICEBERG_TABLE = "glue_catalog.trax_io.location_graph"


def select_location_graph_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _DOMAIN)


def transform_to_location_graph(
    df: DataFrame, *, tenant_id: str, extract_date: date, manifest_sha256: str
) -> DataFrame:
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    cleaned = df.filter(nonblank(F.col("HostLocID")))
    location = F.col("HostLocID").cast(T.StringType())
    parent = F.col("HostParentLocID").cast(T.StringType())
    # Empty/blank parent -> null (bridge ``main or None``).
    main = F.when(F.trim(F.coalesce(parent, F.lit(""))) == "", F.lit(None).cast(T.StringType())) \
        .otherwise(parent)
    role = F.when(main.isNotNull() & (main != location), F.lit("outstation")).otherwise(
        F.lit("main")
    )
    ingested_at = datetime.now(UTC).replace(tzinfo=None)
    mapped = (
        cleaned.withColumn("location", location)
        .withColumn("related_main_warehouse", main)
        .withColumn("role", role)
        .withColumn("children", F.array().cast(T.ArrayType(T.StringType())))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return dedupe_first(mapped, ["location"]).select(*LOCATION_GRAPH_COLUMNS)


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
    job.init(f"location-graph-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    artifacts = select_location_graph_artifacts(manifest)
    if not artifacts:
        LOG.warning("no succeeded location_master artifact in manifest; nothing to do")
        job.commit()
        return

    feature_df = transform_to_location_graph(
        read_artifacts(spark, artifacts),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    append_iceberg(feature_df, _ICEBERG_TABLE)
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
