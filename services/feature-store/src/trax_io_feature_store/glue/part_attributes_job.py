"""PySpark Glue job: raw part_master JSON  -->  `part_attributes` Iceberg table.

Source: extract domain ``part_master`` (#15), one row per PN. Mirrors the reco
``extract_loader`` derivations so the production lake and the shadow-mode bridge agree:
``part_class`` from kit/serializable/repairable flags, hazmat / tool-control as booleans,
and non-positive shelf-life / tail-count collapsed to null.
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
    from pyspark.sql import Column, DataFrame

LOG = logging.getLogger("trax_io.glue.part_attributes")

PART_ATTRIBUTES_COLUMNS: tuple[str, ...] = (
    "pn",
    "description",
    "ata_chapter",
    "part_class",
    "shelf_life_days",
    "hazardous_material",
    "tool_control_item",
    "fleet_effectivity_tail_count",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_DOMAIN: frozenset[str] = frozenset({"part_master"})
_ICEBERG_TABLE = "glue_catalog.trax_io.part_attributes"
_TRUTHY = ("Y", "YES", "TRUE", "1")


def select_part_attributes_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _DOMAIN)


def _truthy(col_name: str) -> Column:
    from pyspark.sql import functions as F  # noqa: N812

    return F.coalesce(F.upper(F.col(col_name)).isin(*_TRUTHY), F.lit(False))


def _positive_int(col_name: str) -> Column:
    """Rounded int; zero/negative/null collapse to null (loader does ``_i(..) or None``, and
    negatives would violate the downstream ``NonNegativeInt`` schema, so they null too)."""
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    rounded = coerce_int(F.col(col_name), 0)
    return F.when(rounded > 0, rounded).otherwise(F.lit(None).cast(T.IntegerType()))


def transform_to_part_attributes(
    df: DataFrame, *, tenant_id: str, extract_date: date, manifest_sha256: str
) -> DataFrame:
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    cleaned = df.filter(F.col("HostPartID").isNotNull())
    part_class = (
        F.when(_truthy("IsPartKit"), F.lit("rotable"))
        .when(_truthy("PartSerializable") | _truthy("PartRepairable"), F.lit("repairable"))
        .otherwise(F.lit("expendable"))
    )
    ingested_at = datetime.now(UTC).replace(tzinfo=None)
    mapped = (
        cleaned.withColumn("pn", F.col("HostPartID").cast(T.StringType()))
        .withColumn("description", F.col("PartDescription").cast(T.StringType()))
        .withColumn("ata_chapter", F.col("ATAChapter").cast(T.StringType()))
        .withColumn("part_class", part_class)
        .withColumn("shelf_life_days", _positive_int("ShelfLife"))
        .withColumn("hazardous_material", _truthy("Hazmat"))
        .withColumn("tool_control_item", _truthy("Tool"))
        .withColumn("fleet_effectivity_tail_count", _positive_int("NoOfTails"))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return mapped.dropDuplicates(["pn"]).select(*PART_ATTRIBUTES_COLUMNS)


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
    job.init(f"part-attributes-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    artifacts = select_part_attributes_artifacts(manifest)
    if not artifacts:
        LOG.warning("no succeeded part_master artifact in manifest; nothing to do")
        job.commit()
        return

    feature_df = transform_to_part_attributes(
        read_artifacts(spark, artifacts),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    append_iceberg(feature_df, _ICEBERG_TABLE)
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
