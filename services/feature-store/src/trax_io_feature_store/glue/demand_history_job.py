"""PySpark Glue job: raw demand-history JSON landing  -->  `demand_history` Iceberg table.

This is the Phase 2 template slice for sub-project #2 (Feature Store & Data Lake).
It establishes the pattern that the remaining 9 feature-group jobs will follow.

Data flow
---------

    s3://<landing>/extract_date=YYYY-MM-DD/run_id=<ulid>/manifest.json
        -> select artifacts where
               domain in {"demand_history_rotables", "demand_history_expendables"}
               and status == "succeeded"
        -> read each artifact's s3_uri (JSON)
        -> transform: normalize columns, bucket by month,
           aggregate removals (rotable) vs issues (expendable)
        -> append to glue_catalog.trax_io.demand_history
           partitioned by (tenant_id, extract_date)

The heavy-lifting helpers are pure functions and are unit-testable with a
local SparkSession. `awsglue` is imported lazily inside the script's
`if __name__ == "__main__"` block so this module can be imported in
environments where the Glue Python stubs are not installed.

TODO(Phase 2+): apply interchangeability rollup (populate
`interchange_group_id`). The column is intentionally left null here --
interchange rollup consumes `interchangeable_graph` feature group which is
a separate template slice.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from trax_io_feature_store.demand import demand_observation_window
from trax_io_feature_store.glue._common import (
    append_feature_group,
    coerce_int,
    disable_ansi_mode,
    finite_double,
    iceberg_table_identifier,
    nonblank,
    normalize_planning_keys,
    read_planning_key_artifacts,
    select_artifacts,
    validate_manifest_identity,
    verify_artifact_integrity,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import DataFrame, SparkSession

LOG = logging.getLogger("trax_io.glue.demand_history")


# ---------------------------------------------------------------------------
# Constants: Iceberg target schema for `demand_history`.
# Mirrors `infra/feature-store/stacks/iceberg_schemas.py::FEATURE_GROUP_SCHEMAS["demand_history"]`
# augmented with the partition columns (tenant_id, extract_date).
# Order MATTERS -- the transform emits columns in exactly this order so the
# Iceberg append is positional-safe.
# ---------------------------------------------------------------------------

DEMAND_HISTORY_COLUMNS: tuple[str, ...] = (
    "pn",
    "location",
    "interchange_group_id",
    "bucket",
    "period_start",
    "removals",
    "issues",
    "removal_events",
    "issue_events",
    "observation_start",
    "observation_end",
    "event_count_source",
    "source",
    "manifest_sha256",
    "ingested_at",
    # partition columns
    "tenant_id",
    "extract_date",
)

_DEMAND_DOMAINS: frozenset[str] = frozenset(
    {"demand_history_rotables", "demand_history_expendables"}
)
_PLANNING_KEY_DOMAINS: frozenset[str] = frozenset(
    {"stock_amount", "stock_level_upload"}
)

_ICEBERG_TABLE = "glue_catalog.trax_io.demand_history"
_EVOLVED_DEMAND_COLUMNS: tuple[tuple[str, str], ...] = (
    ("removal_events", "int"),
    ("issue_events", "int"),
    ("observation_start", "date"),
    ("observation_end", "date"),
    ("event_count_source", "string"),
)


# ---------------------------------------------------------------------------
# Pure helpers -- unit-testable without Spark
# ---------------------------------------------------------------------------


def select_demand_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter a parsed manifest's artifacts to the demand-history domains.

    Returns artifacts with ``status == "succeeded"`` and
    ``domain in {"demand_history_rotables", "demand_history_expendables"}``.
    Returns an empty list (not an error) when neither domain succeeded --
    the degraded-run decision is made upstream by the orchestrator.
    """
    artifacts = manifest.get("artifacts") or []
    out: list[dict[str, Any]] = []
    for a in artifacts:
        if not isinstance(a, dict):
            continue
        if a.get("domain") in _DEMAND_DOMAINS and a.get("status") == "succeeded":
            out.append(a)
    return out


def select_complete_demand_artifacts(
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return succeeded demand artifacts only when both source domains are covered.

    A partial extract cannot distinguish true zero demand from rows omitted by the
    failed domain, so the production job must not append it as observed history.
    """

    artifacts = select_demand_artifacts(manifest)
    covered = {artifact.get("domain") for artifact in artifacts}
    return artifacts if covered == _DEMAND_DOMAINS else []


def select_planning_key_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Successful physical-stock and planning-policy artifacts define the key universe.

    Network-pooled runs can carry planning locations only in
    ``stock_level_upload``; ordinary runs can fall back to ``stock_amount``.
    """

    return select_artifacts(manifest, _PLANNING_KEY_DOMAINS)


# ---------------------------------------------------------------------------
# Spark-dependent helpers
# ---------------------------------------------------------------------------


def load_manifest(spark: SparkSession, manifest_s3_uri: str) -> dict[str, Any]:
    """Read the manifest.json via Spark and return the parsed dict.

    Spark is used (not boto3) so the job's IAM role is the only credential
    path and the same S3A/KMS config applies as for the artifact reads.
    """
    # `wholeTextFiles` returns RDD[(path, content)]; one-element read.
    rdd = spark.sparkContext.wholeTextFiles(manifest_s3_uri)
    pairs = rdd.collect()
    if not pairs:
        raise FileNotFoundError(f"manifest not found at {manifest_s3_uri}")
    _, raw = pairs[0]
    return json.loads(raw)


def read_raw(spark: SparkSession, artifacts: list[dict[str, Any]]) -> DataFrame:
    """Read each artifact's JSON file and union them, tagging with source_domain.

    Adds a string column `source_domain` to each row so the transform can
    cleanly split removals (rotables) vs issues (expendables). The explicit
    source schema is load-bearing: Spark cannot infer columns from a valid
    empty JSON artifact, but an empty successful demand pair must still flow
    through the transform so planning keys receive observed-zero markers.
    """
    from pyspark.sql import functions as F  # noqa: N812  -- PySpark convention
    from pyspark.sql import types as T  # noqa: N812  -- PySpark convention

    if not artifacts:
        raise ValueError("read_raw called with no artifacts -- callers should guard")

    raw_schema = T.StructType(
        [
            T.StructField("HostPartID", T.StringType(), True),
            T.StructField("HostLocID", T.StringType(), True),
            T.StructField("HistoryBegDate", T.StringType(), True),
            T.StructField("HistoryAmount", T.StringType(), True),
        ]
    )
    frames: list[DataFrame] = []
    for a in artifacts:
        s3_uri = a.get("s3_uri")
        domain = a.get("domain")
        if not s3_uri or not domain:
            LOG.warning("artifact missing s3_uri/domain, skipping: %r", a)
            continue
        df = (
            spark.read.schema(raw_schema)
            .option("mode", "FAILFAST")
            .json(s3_uri)
        )
        verify_artifact_integrity(spark, a, df)
        df = df.withColumn("source_domain", F.lit(domain))
        frames.append(df)

    if not frames:
        raise ValueError("no readable artifacts after filtering")

    # `unionByName` with allowMissingColumns handles the rotable/expendable
    # schemas diverging on secondary columns (e.g., TailNumber, DemandNote).
    out = frames[0]
    for df in frames[1:]:
        out = out.unionByName(df, allowMissingColumns=True)
    return out


def transform_to_feature_group(
    df: DataFrame,
    *,
    tenant_id: str,
    extract_date: date,
    manifest_sha256: str,
    observation_start: date | None = None,
    observation_end: date | None = None,
    planning_keys: DataFrame | None = None,
    planning_active_only: bool = False,
) -> DataFrame:
    """Core transform: raw rotable+expendable rows --> demand_history rows.

    * Drops rows with null ``HostPartID`` or ``HistoryBegDate`` (logs count).
    * Buckets to monthly granularity via ``date_trunc('month', HistoryBegDate)``.
    * Aggregates removals (rotables) and issues (expendables) separately
      per ``(pn, location, period_start)``.
    * When a configured observation interval and stock-key DataFrame are supplied,
      emits one zero-valued marker at ``observation_start`` for every stock key that
      has no demand rows. This preserves genuine zero-demand histories in Iceberg;
      missing/non-stock keys still remain absent.
    * Populates metadata columns (source, manifest_sha256, ingested_at,
      tenant_id, extract_date). ``interchange_group_id`` is nulled out --
      interchange rollup is a later template slice.
    * Emits columns in the exact order of ``DEMAND_HISTORY_COLUMNS``.
    """
    from pyspark.sql import functions as F  # noqa: N812  -- PySpark convention
    from pyspark.sql import types as T  # noqa: N812  -- PySpark convention

    # HistoryBegDate comes from Oracle as a string ("mm/dd/yyyy HH24:MI") or
    # an ISO timestamp depending on the driver. We parse defensively.
    # ANSI mode is disabled at job startup, so Spark 3.3's `to_timestamp`
    # yields NULL for unparseable input. Try the Oracle-native
    # `mm/dd/yyyy HH24:MI` format first, then ISO-8601.
    parsed = df.withColumn(
        "_hbg_ts",
        F.coalesce(
            F.to_timestamp(F.col("HistoryBegDate"), "MM/dd/yyyy HH:mm"),
            F.to_timestamp(F.col("HistoryBegDate")),  # ISO fallback
        ),
    ).withColumn("period_start", F.to_date(F.date_trunc("month", F.col("_hbg_ts"))))
    amount = F.col("HistoryAmount").cast(T.DoubleType())
    valid = (
        nonblank(F.col("HostPartID"))
        & nonblank(F.col("HostLocID"))
        & F.col("_hbg_ts").isNotNull()
        & nonblank(F.col("HistoryAmount"))
        & finite_double(amount)
    )
    invalid_count = parsed.filter(~valid).count()
    if invalid_count:
        raise ValueError(
            "demand artifacts contain "
            f"{invalid_count} row(s) with invalid identity/date/quantity"
        )
    parsed = parsed.filter(valid)

    # Aggregate: removals come only from rotable source, issues only from expendable.
    agg = (
        parsed.groupBy(
            F.col("HostPartID").alias("pn"),
            F.col("HostLocID").alias("location"),
            F.col("period_start"),
        )
        .agg(
            # HistoryAmount arrives as a string; round each row to int BEFORE summing so this
            # matches the reco bridge (which does ``_i(historyamount)`` per row, then sums).
            # Summing the raw strings then casting would truncate, diverging on fractional qtys.
            F.sum(
                F.when(
                    F.col("source_domain") == F.lit("demand_history_rotables"),
                    coerce_int(F.col("HistoryAmount"), 0),
                ).otherwise(F.lit(0))
            ).alias("removals"),
            F.sum(
                F.when(
                    F.col("source_domain") == F.lit("demand_history_expendables"),
                    coerce_int(F.col("HistoryAmount"), 0),
                ).otherwise(F.lit(0))
            ).alias("issues"),
            F.sum(
                F.when(
                    (F.col("source_domain") == F.lit("demand_history_rotables"))
                    & (coerce_int(F.col("HistoryAmount"), 0) > F.lit(0)),
                    F.lit(1),
                ).otherwise(F.lit(0))
            ).alias("removal_events"),
            F.sum(
                F.when(
                    (F.col("source_domain") == F.lit("demand_history_expendables"))
                    & (coerce_int(F.col("HistoryAmount"), 0) > F.lit(0)),
                    F.lit(1),
                ).otherwise(F.lit(0))
            ).alias("issue_events"),
        )
        # Downcast Spark's long sums to the Iceberg int columns.
        .withColumn("removals", F.col("removals").cast(T.IntegerType()))
        .withColumn("issues", F.col("issues").cast(T.IntegerType()))
        .withColumn("removal_events", F.col("removal_events").cast(T.IntegerType()))
        .withColumn("issue_events", F.col("issue_events").cast(T.IntegerType()))
    )

    if (
        planning_keys is not None
        and observation_start is not None
        and observation_end is not None
    ):
        stock_keys = normalize_planning_keys(
            planning_keys,
            planning_active_only=planning_active_only,
        )
        demand_keys = agg.select("pn", "location").dropDuplicates(["pn", "location"])
        zero_markers = (
            stock_keys.join(demand_keys, ["pn", "location"], "left_anti")
            .withColumn("period_start", F.lit(observation_start).cast(T.DateType()))
            .withColumn("removals", F.lit(0).cast(T.IntegerType()))
            .withColumn("issues", F.lit(0).cast(T.IntegerType()))
            .withColumn("removal_events", F.lit(0).cast(T.IntegerType()))
            .withColumn("issue_events", F.lit(0).cast(T.IntegerType()))
        )
        agg = agg.unionByName(zero_markers)

    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)

    enriched = (
        agg.withColumn("interchange_group_id", F.lit(None).cast(T.StringType()))
        .withColumn("bucket", F.lit("month"))
        .withColumn(
            "observation_start",
            F.lit(observation_start).cast(T.DateType()),
        )
        .withColumn(
            "observation_end",
            F.lit(observation_end).cast(T.DateType()),
        )
        .withColumn("event_count_source", F.lit("observed"))
        .withColumn("source", F.lit("nightly-extract"))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )

    # Emit exactly the target columns, in order.
    return enriched.select(*[F.col(c) for c in DEMAND_HISTORY_COLUMNS])


def write_iceberg(
    df: DataFrame,
    *,
    lake_bucket: str,
    tenant_id: str,
    table: str = _ICEBERG_TABLE,
) -> None:
    """Append the feature-group DataFrame to the Iceberg table.

    The table is pre-created by the CDK stack at
    ``glue_catalog.trax_io.demand_history`` and partitioned by
    ``(tenant_id, extract_date)``.
    """
    # `lake_bucket` and `tenant_id` are currently unused by the writer path
    # because the table metadata already embeds the S3 location; they are
    # kept in the signature so callers can pass them through from Glue args
    # without re-reading stack outputs.
    _ = lake_bucket, tenant_id

    ensure_demand_history_schema(df.sparkSession, table=table)
    df.writeTo(table).option("write-format", "parquet").append()


def ensure_demand_history_schema(
    spark: SparkSession,
    *,
    table: str = _ICEBERG_TABLE,
) -> None:
    """Evolve retained pre-Phase-1 Iceberg tables before the first new append.

    Updating the Glue/CDK storage descriptor alone does not update an existing
    Iceberg table's metadata schema. The job therefore performs an idempotent
    DESCRIBE/ALTER migration for the five additive nullable columns.
    """

    described = spark.sql(f"DESCRIBE TABLE {table}").collect()
    existing: set[str] = set()
    for row in described:
        if hasattr(row, "asDict"):
            raw_name = row.asDict().get("col_name")
        elif isinstance(row, dict):
            raw_name = row.get("col_name")
        else:
            raw_name = row[0] if row else None
        if raw_name:
            existing.add(str(raw_name).strip().lower())

    missing = [
        (name, column_type)
        for name, column_type in _EVOLVED_DEMAND_COLUMNS
        if name not in existing
    ]
    if not missing:
        return
    definitions = ", ".join(f"{name} {column_type}" for name, column_type in missing)
    spark.sql(f"ALTER TABLE {table} ADD COLUMNS ({definitions})")


# ---------------------------------------------------------------------------
# Glue job entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> dict[str, str]:
    """Minimal Glue-args parser. Glue passes `--key value` pairs."""
    out: dict[str, str] = {}
    it = iter(argv)
    for tok in it:
        if tok.startswith("--"):
            key = tok[2:]
            try:
                val = next(it)
            except StopIteration as exc:
                raise ValueError(f"missing value for arg {tok!r}") from exc
            out[key] = val
    required = {
        "tenant_id",
        "extract_date",
        "landing_bucket",
        "lake_bucket",
        "manifest_s3_uri",
    }
    missing = required - out.keys()
    if missing:
        raise ValueError(f"missing required args: {sorted(missing)}")
    return out


def main(argv: list[str] | None = None) -> None:
    """Glue-runnable entry point.

    Imports `awsglue` lazily so the module is import-safe in test
    environments that do not ship the Glue Python stubs.
    """
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # Lazy imports -- awsglue + pyspark runtime only live inside the Glue container.
    from awsglue.context import GlueContext  # type: ignore[import-not-found]
    from awsglue.job import Job  # type: ignore[import-not-found]
    from pyspark.context import SparkContext

    sc = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    disable_ansi_mode(spark)
    job = Job(glue_ctx)
    job.init(f"demand-history-{args['tenant_id']}-{args['extract_date']}", args)

    tenant_id = args["tenant_id"]
    extract_date = date.fromisoformat(args["extract_date"])
    manifest_s3_uri = args["manifest_s3_uri"]

    LOG.info("loading manifest: %s", manifest_s3_uri)
    manifest = load_manifest(spark, manifest_s3_uri)
    validate_manifest_identity(
        manifest,
        tenant_id=tenant_id,
        extract_date=extract_date,
    )
    artifacts = select_complete_demand_artifacts(manifest)
    if not artifacts:
        LOG.warning(
            "demand-history artifacts are absent or incomplete; refusing to append "
            "partial demand as observed history"
        )
        job.commit()
        return

    manifest_sha256 = str(manifest.get("source_sql_sha256") or "")
    observation_window = demand_observation_window(manifest)
    planning_key_artifacts = select_planning_key_artifacts(manifest)
    planning_keys = (
        read_planning_key_artifacts(spark, planning_key_artifacts)
        if planning_key_artifacts
        else None
    )

    raw = read_raw(spark, artifacts)
    feature_df = transform_to_feature_group(
        raw,
        tenant_id=tenant_id,
        extract_date=extract_date,
        manifest_sha256=manifest_sha256,
        observation_start=observation_window[0] if observation_window else None,
        observation_end=observation_window[1] if observation_window else None,
        planning_keys=planning_keys,
        planning_active_only=bool(
            manifest.get("pool_by_part")
            or manifest.get("scope_mode") in {
                "planning_active",
                "network_planning_active",
            }
        ),
    )
    target_table = iceberg_table_identifier(args, "demand_history")
    ensure_demand_history_schema(spark, table=target_table)
    append_feature_group(
        feature_df,
        target_table=target_table,
        status_table=iceberg_table_identifier(args, "feature_batch_status"),
        feature_group="demand_history",
        run_id=str(manifest.get("run_id") or ""),
        tenant_id=tenant_id,
        extract_date=extract_date,
        manifest_sha256=manifest_sha256,
    )

    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
