"""PySpark Glue job: raw part_master JSON  -->  `criticality` Iceberg table.

Source: extract domain ``part_master`` (#15), ``HostPartCriticalID`` is the raw essentiality
code. It is normalized to the canonical 1..5 tier via the same default map the reco
``extract_loader`` uses (design §4.3, tenant-overridable). ``raw_essentiality_code`` keeps the
original case; only the map lookup upper-cases. ``mapping_source`` is ``auto_inferred``.
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
    read_artifacts,
    select_artifacts,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import DataFrame

LOG = logging.getLogger("trax_io.glue.criticality")

CRITICALITY_COLUMNS: tuple[str, ...] = (
    "pn",
    "raw_essentiality_code",
    "canonical_tier",
    "mapping_source",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_DOMAIN: frozenset[str] = frozenset({"part_master"})
_ICEBERG_TABLE = "glue_catalog.trax_io.criticality"
_DEFAULT_TIER = 4

# Default essentiality-code -> canonical 1..5 tier map (design §4.3, tenant-overridable).
# Kept in lock-step with reco ``extract_loader._DEFAULT_ESSENTIALITY_MAP``.
_ESSENTIALITY_TIER: dict[str, int] = {
    "1": 1, "AOG": 1, "NG": 1, "NOGO": 1, "NO-GO": 1, "NO_GO": 1,
    "2": 2, "GO-IF": 2, "GOIF": 2, "GO_IF": 2,
    "3": 3, "DISPATCH": 3,
    "4": 4, "ROUTINE": 4,
    "5": 5, "CONSUMABLE": 5, "NON-CRITICAL": 5,
}


def select_criticality_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _DOMAIN)


def transform_to_criticality(
    df: DataFrame, *, tenant_id: str, extract_date: date, manifest_sha256: str
) -> DataFrame:
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    cleaned = df.filter(F.col("HostPartID").isNotNull())
    trimmed = F.trim(F.coalesce(F.col("HostPartCriticalID").cast(T.StringType()), F.lit("")))
    raw_code = F.when(trimmed == "", F.lit("0")).otherwise(trimmed)
    lookup = F.upper(raw_code)

    items = list(_ESSENTIALITY_TIER.items())
    tier = F.when(lookup == items[0][0], F.lit(items[0][1]))
    for code, value in items[1:]:
        tier = tier.when(lookup == code, F.lit(value))
    tier = tier.otherwise(F.lit(_DEFAULT_TIER)).cast(T.IntegerType())

    ingested_at = datetime.now(UTC).replace(tzinfo=None)
    mapped = (
        cleaned.withColumn("pn", F.col("HostPartID").cast(T.StringType()))
        .withColumn("raw_essentiality_code", raw_code)
        .withColumn("canonical_tier", tier)
        .withColumn("mapping_source", F.lit("auto_inferred"))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return mapped.dropDuplicates(["pn"]).select(*CRITICALITY_COLUMNS)


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
    job.init(f"criticality-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    artifacts = select_criticality_artifacts(manifest)
    if not artifacts:
        LOG.warning("no succeeded part_master artifact in manifest; nothing to do")
        job.commit()
        return

    feature_df = transform_to_criticality(
        read_artifacts(spark, artifacts),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    append_iceberg(feature_df, _ICEBERG_TABLE)
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
