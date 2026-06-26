"""PySpark Glue job: raw stock_level_upload JSON landing  -->  `current_policy` Iceberg table.

Source: extract domain ``stock_level_upload`` (#19), one row per (PN, Location) carrying the
existing PN_INVENTORY_LEVEL values. The #19 SQL alias transposition is corrected at the source,
so HostPartID=PN and HostLocID=LOCATION here. Column order mirrors the Iceberg schema map +
partition columns.
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

LOG = logging.getLogger("trax_io.glue.current_policy")

CURRENT_POLICY_COLUMNS: tuple[str, ...] = (
    "pn",
    "location",
    "rop",
    "eoq",
    "safety_stock",
    "max_stock",
    "replenishment_lead_days",
    "manifest_sha256",
    "ingested_at",
    # partition columns
    "tenant_id",
    "extract_date",
)

_DOMAIN: frozenset[str] = frozenset({"stock_level_upload"})
_ICEBERG_TABLE = "glue_catalog.trax_io.current_policy"


def select_current_policy_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _DOMAIN)


def transform_to_current_policy(
    df: DataFrame, *, tenant_id: str, extract_date: date, manifest_sha256: str
) -> DataFrame:
    """Map raw stock_level_upload rows to current_policy.

    rop<-rop, eoq<-eoq, safety_stock<-safetylevel, max_stock<-stockmax,
    replenishment_lead_days<-slreplenishmentlength. Deduped on (pn, location).
    """
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    def _i(col: str):  # noqa: ANN202
        # Round-then-int (bridge ``_i`` parity); a bare int cast would truncate string levels.
        return coerce_int(F.col(col), 0)

    cleaned = df.filter(F.col("HostPartID").isNotNull() & F.col("HostLocID").isNotNull())
    ingested_at = datetime.now(UTC).replace(tzinfo=None)
    mapped = (
        cleaned.withColumn("pn", F.col("HostPartID").cast(T.StringType()))
        .withColumn("location", F.col("HostLocID").cast(T.StringType()))
        .withColumn("rop", _i("rop"))
        .withColumn("eoq", _i("eoq"))
        .withColumn("safety_stock", _i("safetylevel"))
        .withColumn("max_stock", _i("stockmax"))
        .withColumn(
            "replenishment_lead_days",
            F.coalesce(F.col("slreplenishmentlength").cast(T.DoubleType()), F.lit(0.0)),
        )
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return mapped.dropDuplicates(["pn", "location"]).select(*CURRENT_POLICY_COLUMNS)


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
    job.init(f"current-policy-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    artifacts = select_current_policy_artifacts(manifest)
    if not artifacts:
        LOG.warning("no succeeded stock_level_upload artifact in manifest; nothing to do")
        job.commit()
        return

    feature_df = transform_to_current_policy(
        read_artifacts(spark, artifacts),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    append_iceberg(feature_df, _ICEBERG_TABLE)
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
