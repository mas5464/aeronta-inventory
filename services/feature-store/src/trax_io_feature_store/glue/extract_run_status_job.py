"""Persist one manifest-coherence row per tenant extract run.

Feature tables are append-only. Without a run ledger, a missing/failed newest
artifact leaves an older successful feature row looking current. This job
records the authoritative per-domain manifest status so Iceberg readers can
pin every lookup to the newest tenant snapshot and fail closed when that
snapshot did not successfully produce the requested source domain.
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from trax_io_feature_store.glue._common import (
    append_iceberg,
    disable_ansi_mode,
    iceberg_table_identifier,
    load_manifest,
    validate_manifest_identity,
)

LOG = logging.getLogger("trax_io.glue.extract_run_status")

EXTRACT_RUN_STATUS_COLUMNS: tuple[str, ...] = (
    "run_id",
    "run_status",
    "artifact_status_json",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_ICEBERG_TABLE = "glue_catalog.trax_io.extract_run_status"


def canonical_artifact_statuses(manifest: dict[str, Any]) -> dict[str, str]:
    """Collapse duplicate artifact records without allowing success to mask failure."""

    by_domain: dict[str, list[str]] = defaultdict(list)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        domain = artifact.get("domain")
        status = artifact.get("status")
        if not isinstance(domain, str) or not domain.strip():
            continue
        by_domain[domain].append(str(status or "unknown"))

    statuses: dict[str, str] = {}
    for domain, values in by_domain.items():
        unique = set(values)
        if unique == {"succeeded"}:
            statuses[domain] = "succeeded"
        elif len(unique) == 1:
            statuses[domain] = values[0]
        else:
            statuses[domain] = "conflict"
    return statuses


def transform_manifest_status(
    spark: Any,
    manifest: dict[str, Any],
    *,
    tenant_id: str,
    extract_date: date,
) -> Any:
    """Build the single typed ledger row written for this manifest."""

    from pyspark.sql import types as T  # noqa: N812

    schema = T.StructType(
        [
            T.StructField("run_id", T.StringType(), False),
            T.StructField("run_status", T.StringType(), False),
            T.StructField("artifact_status_json", T.StringType(), False),
            T.StructField("manifest_sha256", T.StringType(), False),
            T.StructField("ingested_at", T.TimestampType(), False),
            T.StructField("tenant_id", T.StringType(), False),
            T.StructField("extract_date", T.DateType(), False),
        ]
    )
    row = (
        str(manifest.get("run_id") or ""),
        str(manifest.get("run_status") or "unknown"),
        json.dumps(
            canonical_artifact_statuses(manifest),
            sort_keys=True,
            separators=(",", ":"),
        ),
        str(manifest.get("source_sql_sha256") or ""),
        datetime.now(timezone.utc).replace(tzinfo=None),
        tenant_id,
        extract_date,
    )
    return spark.createDataFrame([row], schema).select(
        *EXTRACT_RUN_STATUS_COLUMNS
    )


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
        f"extract-run-status-{args['tenant_id']}-{args['extract_date']}",
        args,
    )

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    validate_manifest_identity(
        manifest,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
    )
    status_df = transform_manifest_status(
        spark,
        manifest,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
    )
    append_iceberg(
        status_df,
        iceberg_table_identifier(args, "extract_run_status"),
    )
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
