"""Shared helpers for the per-feature-group Glue jobs.

Single-domain jobs (e.g. stock_position, current_policy) reuse these; the demand_history
template job keeps its own source-domain-tagging read because it unions two domains.
"""

from __future__ import annotations

import json
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


def read_artifacts(spark: SparkSession, artifacts: list[dict[str, Any]]) -> DataFrame:
    """Read each artifact's s3_uri JSON and union them (allowMissingColumns)."""
    if not artifacts:
        raise ValueError("read_artifacts called with no artifacts -- callers should guard")
    frames: list[DataFrame] = []
    for a in artifacts:
        s3_uri = a.get("s3_uri")
        if s3_uri:
            frames.append(spark.read.json(s3_uri))
    if not frames:
        raise ValueError("no readable artifacts after filtering")
    out = frames[0]
    for df in frames[1:]:
        out = out.unionByName(df, allowMissingColumns=True)
    return out


def append_iceberg(df: DataFrame, table: str) -> None:
    """Append the feature-group DataFrame to a pre-created Iceberg table
    (partitioned by ``(tenant_id, extract_date)`` by the CDK stack)."""
    df.writeTo(table).option("write-format", "parquet").append()


def disable_ansi_mode(spark: SparkSession) -> None:
    """Pin ANSI off so cast semantics match Glue 4.0 / Spark 3.3 production.

    The extract delivers every numeric field as a *string*. Under ANSI mode (the default in
    Spark 4.x, used by the local test JVM) a malformed value crashes the whole job on cast;
    under ANSI-off (Glue 4.0's default) it yields null. The shadow-mode reco bridge (`_i`/`_f`/
    `_dec`) is forgiving and returns a default on bad input, so ANSI-off is the faithful mode.
    Tests set the same flag on their SparkSession so they mirror production.
    """
    spark.conf.set("spark.sql.ansi.enabled", "false")


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
