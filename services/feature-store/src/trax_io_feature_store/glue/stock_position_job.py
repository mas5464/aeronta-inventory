"""PySpark Glue job: raw stock_amount JSON landing  -->  `stock_position` Iceberg table.

Source: extract domain ``stock_amount`` (#18), one snapshot row per (PN, Location).
Column order mirrors infra/feature-store/stacks/iceberg_schemas.py::FEATURE_GROUP_SCHEMAS
["stock_position"] + the partition columns; the transform emits that exact order so the
Iceberg append is positional-safe.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from trax_io_feature_store.glue._common import (
    append_feature_group,
    coerce_int,
    disable_ansi_mode,
    iceberg_table_identifier,
    load_manifest,
    nonblank,
    read_artifacts,
    select_artifacts,
    valid_optional_int,
    validate_manifest_identity,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import DataFrame

LOG = logging.getLogger("trax_io.glue.stock_position")

STOCK_POSITION_COLUMNS: tuple[str, ...] = (
    "pn",
    "location",
    "on_hand",
    "serviceable",
    "unserviceable_in_repair",
    "allocated_reserved",
    "rental",
    "loan",
    "manifest_sha256",
    "ingested_at",
    # partition columns
    "tenant_id",
    "extract_date",
)

_DOMAIN: frozenset[str] = frozenset({"stock_amount"})
_ICEBERG_TABLE = "glue_catalog.trax_io.stock_position"


def select_stock_position_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _DOMAIN)


def transform_to_stock_position(
    df: DataFrame, *, tenant_id: str, extract_date: date, manifest_sha256: str
) -> DataFrame:
    """Map raw stock_amount rows to the stock_position feature group.

    serviceable<-OnHandNew, unserviceable_in_repair<-InRepair, allocated_reserved<-Allocated,
    rental<-RentalQty, loan<-LoanQty, on_hand<-OnHandNew+OnHandBad+InRepair. Deduped on
    (pn, location). Column resolution is case-insensitive, so lowercased extract aliases match.
    """
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    def _i(col: str):  # noqa: ANN202
        # Round-then-int (bridge ``_i`` parity); a bare int cast would truncate string qtys.
        return coerce_int(F.col(col), 0)

    valid = nonblank(F.col("HostPartID")) & nonblank(F.col("HostLocID"))
    for source_column in (
        "OnHandNew",
        "OnHandBad",
        "InRepair",
        "Allocated",
        "RentalQty",
        "LoanQty",
    ):
        valid = valid & valid_optional_int(F.col(source_column))
    invalid_rows = df.filter(~valid).count()
    if invalid_rows:
        raise ValueError(
            f"stock_amount contains {invalid_rows} row(s) with invalid required fields"
        )

    cleaned = df
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    mapped = (
        cleaned.withColumn("pn", F.col("HostPartID").cast(T.StringType()))
        .withColumn("location", F.col("HostLocID").cast(T.StringType()))
        .withColumn("on_hand", _i("OnHandNew") + _i("OnHandBad") + _i("InRepair"))
        .withColumn("serviceable", _i("OnHandNew"))
        .withColumn("unserviceable_in_repair", _i("InRepair"))
        .withColumn("allocated_reserved", _i("Allocated"))
        .withColumn("rental", _i("RentalQty"))
        .withColumn("loan", _i("LoanQty"))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return mapped.dropDuplicates(["pn", "location"]).select(*STOCK_POSITION_COLUMNS)


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
    job.init(f"stock-position-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    validate_manifest_identity(
        manifest,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
    )
    artifacts = select_stock_position_artifacts(manifest)
    if not artifacts:
        LOG.warning("no succeeded stock_amount artifact in manifest; nothing to do")
        job.commit()
        return

    feature_df = transform_to_stock_position(
        read_artifacts(spark, artifacts),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    append_feature_group(
        feature_df,
        target_table=iceberg_table_identifier(args, "stock_position"),
        status_table=iceberg_table_identifier(args, "feature_batch_status"),
        feature_group="stock_position",
        run_id=str(manifest.get("run_id") or ""),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
