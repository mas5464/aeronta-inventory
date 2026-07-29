"""Shared helpers for the per-feature-group Glue jobs.

Single-domain jobs (e.g. stock_position, current_policy) reuse these; the demand_history
template job keeps its own source-domain-tagging read because it unions two domains.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import Column, DataFrame, SparkSession


def load_manifest(spark: SparkSession, manifest_s3_uri: str) -> dict[str, Any]:
    """Read manifest.json via Spark (so the Glue IAM role is the only credential path)."""
    pairs = spark.sparkContext.wholeTextFiles(manifest_s3_uri).collect()
    if not pairs:
        raise FileNotFoundError(f"manifest not found at {manifest_s3_uri}")
    _, raw = pairs[0]
    return json.loads(raw)


def select_artifacts(manifest: dict[str, Any], domains: frozenset[str]) -> list[dict[str, Any]]:
    """Artifacts with status=='succeeded' whose domain is in ``domains``."""
    out: list[dict[str, Any]] = []
    for a in manifest.get("artifacts") or []:
        if isinstance(a, dict) and a.get("domain") in domains and a.get("status") == "succeeded":
            out.append(a)
    return out


def validate_manifest_identity(
    manifest: dict[str, Any],
    *,
    tenant_id: str,
    extract_date: date,
) -> None:
    """Fail closed when a Glue invocation is not the manifest's exact run.

    Run/batch markers are only meaningful when every job stamps the business
    identity declared by the immutable manifest. A manual or misrouted job
    invocation must not be able to relabel older data as a newer tenant/date.
    """

    schema_version = str(manifest.get("schema_version") or "").strip()
    if schema_version != "1.0.0":
        raise ValueError(
            f"unsupported manifest schema_version {schema_version!r}; expected '1.0.0'"
        )
    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("manifest run_id is required")
    manifest_tenant = str(manifest.get("tenant_id") or "").strip()
    if manifest_tenant != tenant_id:
        raise ValueError(
            f"manifest tenant_id mismatch: manifest={manifest_tenant!r}, invocation={tenant_id!r}"
        )
    raw_extract_date = manifest.get("extract_date")
    try:
        manifest_date = date.fromisoformat(str(raw_extract_date))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"manifest extract_date is invalid: {raw_extract_date!r}"
        ) from exc
    if manifest_date != extract_date:
        raise ValueError(
            "manifest extract_date mismatch: "
            f"manifest={manifest_date.isoformat()}, invocation={extract_date.isoformat()}"
        )
    if not isinstance(manifest.get("artifacts"), list):
        raise ValueError("manifest artifacts must be a list")


def read_artifacts(spark: SparkSession, artifacts: list[dict[str, Any]]) -> DataFrame:
    """Read each artifact's s3_uri JSON and union them (allowMissingColumns)."""
    if not artifacts:
        raise ValueError("read_artifacts called with no artifacts -- callers should guard")
    frames: list[DataFrame] = []
    for a in artifacts:
        s3_uri = a.get("s3_uri")
        if s3_uri:
            frame = spark.read.option("mode", "FAILFAST").json(s3_uri)
            verify_artifact_integrity(spark, a, frame)
            frames.append(frame)
    if not frames:
        raise ValueError("no readable artifacts after filtering")
    out = frames[0]
    for df in frames[1:]:
        out = out.unionByName(df, allowMissingColumns=True)
    return out


def read_artifacts_with_schema(
    spark: SparkSession,
    artifacts: list[dict[str, Any]],
    schema: Any,
) -> DataFrame:
    """Strictly read JSON artifacts with a schema that survives valid ``[]`` files.

    Successful-but-empty optional feeds must remain distinguishable from
    unavailable feeds. Explicit schemas preserve that state while ``FAILFAST``
    prevents malformed JSON from being mislabeled as observed-empty.
    """

    if not artifacts:
        raise ValueError(
            "read_artifacts_with_schema called with no artifacts -- callers should guard"
        )
    frames: list[DataFrame] = []
    for artifact in artifacts:
        s3_uri = artifact.get("s3_uri")
        if s3_uri:
            frame = (
                spark.read.schema(schema)
                .option("mode", "FAILFAST")
                .json(s3_uri)
            )
            verify_artifact_integrity(spark, artifact, frame)
            frames.append(frame)
    if not frames:
        raise ValueError("no readable artifacts after filtering")
    out = frames[0]
    for frame in frames[1:]:
        out = out.unionByName(frame, allowMissingColumns=True)
    return out


def read_planning_key_artifacts(
    spark: SparkSession,
    artifacts: list[dict[str, Any]],
) -> DataFrame:
    """Read the stock/policy key universe with columns present even when rowless."""

    from pyspark.sql import types as T  # noqa: N812

    schema = T.StructType(
        [
            T.StructField("HostPartID", T.StringType(), True),
            T.StructField("HostLocID", T.StringType(), True),
            T.StructField("rop", T.StringType(), True),
            T.StructField("stockmax", T.StringType(), True),
        ]
    )
    frame = read_artifacts_with_schema(spark, artifacts, schema)
    invalid = frame.filter(
        ~(
            nonblank(frame["HostPartID"])
            & nonblank(frame["HostLocID"])
        )
    ).count()
    if invalid:
        raise ValueError(
            f"planning-key artifacts contain {invalid} row(s) with blank identity"
        )
    return frame


def verify_artifact_integrity(
    spark: SparkSession,
    artifact: dict[str, Any],
    frame: DataFrame,
) -> None:
    """Verify manifest row count and raw-byte SHA-256 before trusting a feed."""

    domain = str(artifact.get("domain") or "unknown")
    if "row_count" in artifact and artifact.get("row_count") is not None:
        try:
            expected_rows = int(artifact["row_count"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{domain} artifact has invalid manifest row_count"
            ) from exc
        actual_rows = frame.count()
        if actual_rows != expected_rows:
            raise ValueError(
                f"{domain} artifact row_count mismatch: "
                f"manifest={expected_rows}, actual={actual_rows}"
            )

    expected_sha = artifact.get("sha256")
    if expected_sha is None or str(expected_sha).strip() == "":
        return
    s3_uri = artifact.get("s3_uri")
    if not s3_uri:
        raise ValueError(
            f"{domain} artifact declares sha256 without s3_uri"
        )
    pairs = spark.sparkContext.binaryFiles(str(s3_uri)).collect()
    if len(pairs) != 1:
        raise ValueError(
            f"{domain} artifact checksum expected one object, got {len(pairs)}"
        )
    payload = pairs[0][1].read()
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != str(expected_sha):
        raise ValueError(
            f"{domain} artifact sha256 mismatch: "
            f"manifest={expected_sha}, actual={actual_sha}"
        )


def normalize_planning_keys(
    planning_keys: DataFrame,
    *,
    planning_active_only: bool,
) -> DataFrame:
    """Normalize the manifest-backed planning universe to distinct ``pn/location`` rows."""

    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    key_rows = planning_keys
    if (
        planning_active_only
        and "rop" in key_rows.columns
        and "stockmax" in key_rows.columns
    ):
        key_rows = key_rows.filter(
            (coerce_int(F.col("rop"), 0) > F.lit(0))
            | (coerce_int(F.col("stockmax"), 0) > F.lit(0))
        )
    return (
        key_rows.filter(
            nonblank(F.col("HostPartID")) & nonblank(F.col("HostLocID"))
        )
        .select(
            F.col("HostPartID").cast(T.StringType()).alias("pn"),
            F.col("HostLocID").cast(T.StringType()).alias("location"),
        )
        .dropDuplicates(["pn", "location"])
    )


_ICEBERG_PARTITION_FIELDS = ("tenant_id", "extract_date")
_ICEBERG_IDENTIFIER = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*){1,2}$"
)


def _require_safe_table_identifier(table: str) -> None:
    if not _ICEBERG_IDENTIFIER.fullmatch(table):
        raise ValueError(f"unsafe Iceberg table identifier: {table!r}")


def _iceberg_partition_fields(spark: SparkSession, table: str) -> tuple[str, ...]:
    """Read identity partition fields from Spark's executable table metadata."""

    rows = spark.sql(f"SHOW CREATE TABLE {table}").collect()
    if len(rows) != 1 or not rows[0]:
        raise RuntimeError(f"SHOW CREATE TABLE returned no DDL for {table}")
    ddl = str(rows[0][0])
    match = re.search(
        r"\bPARTITIONED\s+BY\s*\((.*?)\)",
        ddl,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return ()
    return tuple(
        token.strip().strip("`").lower()
        for token in match.group(1).split(",")
        if token.strip()
    )


def ensure_iceberg_partition_spec(df: DataFrame, table: str) -> None:
    """Create or migrate a table to the exact native Iceberg partition spec.

    CloudFormation's ``TableInput.PartitionKeys`` is a catalog declaration, not
    proof that Iceberg metadata contains a ``PartitionSpec``. Every executable
    append therefore verifies Spark's actual table DDL. A missing table is
    created through DataFrameWriterV2 with identity partition transforms; an
    empty unpartitioned table is migrated using Iceberg partition evolution.
    Unexpected or reordered specs fail closed.
    """

    _require_safe_table_identifier(table)
    spark = df.sparkSession
    if not spark.catalog.tableExists(table):
        from pyspark.sql import functions as F  # noqa: N812

        (
            df.limit(0)
            .writeTo(table)
            .using("iceberg")
            .partitionedBy(*(F.col(name) for name in _ICEBERG_PARTITION_FIELDS))
            .tableProperty("format-version", "2")
            .create()
        )

    fields = _iceberg_partition_fields(spark, table)
    allowed_prefixes = {
        (),
        ("tenant_id",),
        _ICEBERG_PARTITION_FIELDS,
    }
    if fields not in allowed_prefixes:
        raise RuntimeError(
            f"{table} has unsafe Iceberg partition spec {fields!r}; "
            f"expected {_ICEBERG_PARTITION_FIELDS!r}"
        )

    for index, field in enumerate(_ICEBERG_PARTITION_FIELDS):
        if len(fields) > index:
            continue
        try:
            spark.sql(f"ALTER TABLE {table} ADD PARTITION FIELD {field}")
        except Exception:
            # Another job may have evolved the shared status table between
            # our SHOW and ALTER. Re-read metadata and accept only the exact
            # expected prefix; permission/catalog errors still propagate.
            concurrent_fields = _iceberg_partition_fields(spark, table)
            if (
                len(concurrent_fields) <= index
                or concurrent_fields[: index + 1]
                != _ICEBERG_PARTITION_FIELDS[: index + 1]
            ):
                raise
        fields = _iceberg_partition_fields(spark, table)
        if fields[: index + 1] != _ICEBERG_PARTITION_FIELDS[: index + 1]:
            raise RuntimeError(
                f"{table} failed Iceberg partition evolution at {field!r}: "
                f"actual={fields!r}"
            )

    if fields != _ICEBERG_PARTITION_FIELDS:
        raise RuntimeError(
            f"{table} has unsafe Iceberg partition spec {fields!r}; "
            f"expected {_ICEBERG_PARTITION_FIELDS!r}"
        )


def append_iceberg(df: DataFrame, table: str) -> None:
    """Verify native Iceberg metadata, then append a feature batch."""

    ensure_iceberg_partition_spec(df, table)
    df.writeTo(table).option("write-format", "parquet").append()


def iceberg_table_identifier(
    args: dict[str, str],
    feature_group: str,
) -> str:
    """Resolve the exact CDK-provisioned Glue/Iceberg identifier.

    Local/legacy callers may omit the deployment arguments and retain the
    historical ``glue_catalog.trax_io.<group>`` identifier.
    """

    database = args.get("catalog_database", "trax_io")
    table_prefix = args.get("table_prefix", "")
    return f"glue_catalog.{database}.{table_prefix}{feature_group}"


def append_feature_group(
    df: DataFrame,
    *,
    target_table: str,
    status_table: str,
    feature_group: str,
    run_id: str,
    tenant_id: str,
    extract_date: date,
    manifest_sha256: str,
) -> None:
    """Append a feature batch, then atomically expose its completion marker.

    Iceberg does not provide a cross-table transaction. Writing the marker
    strictly after the feature append gives readers a fail-closed commit
    protocol: rows without a matching completed marker remain invisible.
    ``batch_ingested_at`` binds same-date reruns to the exact feature batch.
    """

    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    if not run_id.strip():
        raise ValueError(
            f"manifest run_id is required to materialize {feature_group}"
        )
    batch = df.agg(
        F.max(F.col("ingested_at")).alias("batch_ingested_at"),
        F.count(F.lit(1)).alias("row_count"),
    ).collect()[0]
    append_iceberg(df, target_table)

    status_schema = T.StructType(
        [
            T.StructField("feature_group", T.StringType(), False),
            T.StructField("run_id", T.StringType(), False),
            T.StructField("status", T.StringType(), False),
            T.StructField("batch_ingested_at", T.TimestampType(), True),
            T.StructField("row_count", T.LongType(), False),
            T.StructField("manifest_sha256", T.StringType(), False),
            T.StructField("ingested_at", T.TimestampType(), False),
            T.StructField("tenant_id", T.StringType(), False),
            T.StructField("extract_date", T.DateType(), False),
        ]
    )
    status_row = (
        feature_group,
        run_id,
        "completed",
        batch["batch_ingested_at"],
        batch["row_count"],
        manifest_sha256,
        datetime.now(timezone.utc).replace(tzinfo=None),
        tenant_id,
        extract_date,
    )
    status_df = df.sparkSession.createDataFrame(
        [status_row],
        status_schema,
    )
    append_iceberg(status_df, status_table)


def disable_ansi_mode(spark: SparkSession) -> None:
    """Pin ANSI off so cast semantics match Glue 4.0 / Spark 3.3 production.

    The extract delivers every numeric field as a *string*. Under ANSI mode (the default in
    Spark 4.x, used by the local test JVM) a malformed value crashes the whole job on cast;
    under ANSI-off (Glue 4.0's default) it yields null. The shadow-mode reco bridge (`_i`/`_f`/
    `_dec`) is forgiving and returns a default on bad input, so ANSI-off is the faithful mode.
    Tests set the same flag on their SparkSession so they mirror production.
    """
    spark.conf.set("spark.sql.ansi.enabled", "false")


def nonblank(col: Column) -> Column:
    """True when the column is non-null AND not blank/whitespace.

    Mirrors the reco bridge's truthiness key guards (``if not pn`` / ``if not loc``), which
    reject ``""`` as well as null — a bare ``isNotNull()`` would emit junk feature rows with an
    empty key that the shadow bridge never produces.
    """
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    s = col.cast(T.StringType())
    return s.isNotNull() & (F.trim(s) != F.lit(""))


def finite_double(col: Column) -> Column:
    """True only when a value is representable as a finite Spark double."""

    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    number = col.cast(T.DoubleType())
    return (
        number.isNotNull()
        & ~F.isnan(number)
        & (number != F.lit(float("inf")))
        & (number != F.lit(float("-inf")))
    )


def valid_optional_number(
    col: Column,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> Column:
    """Validate an optional numeric source value without silently defaulting corruption.

    Extracts legitimately use null/blank values for optional measures; downstream
    transforms may still apply their documented defaults to those values. A
    nonblank value, however, must be finite and within the feature's semantic
    range so malformed strings and infinities cannot be relabeled as zero.
    """

    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    number = col.cast(T.DoubleType())
    valid = finite_double(col)
    if minimum is not None:
        valid = valid & (number >= F.lit(minimum))
    if maximum is not None:
        valid = valid & (number <= F.lit(maximum))
    return ~nonblank(col) | valid


def valid_optional_int(
    col: Column,
    *,
    minimum: int = 0,
    maximum: int = 2_147_483_647,
) -> Column:
    """Validate an optional source number that will be rounded into Spark ``int``."""

    return valid_optional_number(
        col,
        minimum=float(minimum),
        maximum=float(maximum),
    )


def valid_optional_decimal(
    col: Column,
    decimal_type: str,
    *,
    minimum: float | None = None,
) -> Column:
    """Validate an optional source value before casting it to a bounded decimal."""

    from pyspark.sql import functions as F  # noqa: N812

    decimal_value = col.cast(decimal_type)
    valid = finite_double(col) & decimal_value.isNotNull()
    if minimum is not None:
        valid = valid & (decimal_value >= F.lit(minimum).cast(decimal_type))
    return ~nonblank(col) | valid


def dedupe_first(df: DataFrame, keys: list[str]) -> DataFrame:
    """Deterministic dedup on ``keys``: keep the lexicographically-smallest row by all scalar
    non-key columns.

    A bare ``dropDuplicates`` picks an arbitrary row when duplicate keys carry *different*
    values, so its output varies across runs/partitions — breaking SOC 2 reproducibility and
    diverging from the bridge. Ordering by the scalar columns makes the survivor deterministic
    (complex array/struct columns are skipped — they can't disagree under a fixed key here).
    """
    from pyspark.sql import Window
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql.types import ArrayType, MapType, StructType

    keyset = set(keys)
    order_cols = [
        f.name
        for f in df.schema.fields
        if f.name not in keyset and not isinstance(f.dataType, ArrayType | StructType | MapType)
    ]
    order_by = [F.col(c).asc_nulls_last() for c in (order_cols or keys)]
    w = Window.partitionBy(*keys).orderBy(*order_by)
    return df.withColumn("_rn", F.row_number().over(w)).filter(F.col("_rn") == 1).drop("_rn")


def parse_extract_date(col: Column) -> Column:
    """Parse an extract date string to a Spark ``date``, mirroring the reco bridge ``_parse_date``.

    Honors the Oracle-native ``MM/dd/yyyy[ HH:mm]`` forms and ISO-8601, returning null for
    anything unparseable while ANSI mode is disabled. ``to_timestamp`` is
    available in Glue 4.0's Spark 3.3 runtime; ``try_to_timestamp`` is not.
    The same helper is used by the demand job so date handling stays in lockstep.
    """
    from pyspark.sql import functions as F  # noqa: N812

    ts = F.coalesce(
        F.to_timestamp(col, "MM/dd/yyyy HH:mm"),
        F.to_timestamp(col, "MM/dd/yyyy"),
        F.to_timestamp(col),  # ISO-8601 fallback
    )
    return F.to_date(ts)


def coerce_int(col: Column, default: int = 0) -> Column:
    """Round-then-int coercion mirroring the reco bridge ``_i`` (design parity for shadow mode).

    A bare ``cast(IntegerType())`` on a string *truncates* the fractional part
    (``"365.5" -> 365``), whereas the bridge does ``int(round(float(v)))`` — banker's rounding
    (``"365.5" -> 366``, ``"2.5" -> 2``). ``bround`` is Spark's HALF_EVEN round, matching
    Python's ``round``. Null / unparseable (ANSI-off) collapse to ``default``.
    """
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    rounded = F.bround(col.cast(T.DoubleType())).cast(T.IntegerType())
    return F.coalesce(rounded, F.lit(default))
