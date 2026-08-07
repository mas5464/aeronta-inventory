"""PySpark Glue job: raw part_chain_details JSON  -->  `interchangeable_graph` Iceberg table.

Source: extract domain ``part_chain_details`` (#11), one row per (PN, chain-parent) edge.
Mirrors the reco bridge ``_seed_interchange``: each detail row contributes an edge
``(from_pn=a, to_pn=b, one_way)`` to BOTH endpoints' rows, ``one_way`` is RelationType==1, and
each PN's ``members`` is the distinct set of every PN it shares an edge with (plus itself).
``group_id`` is ``"+".join(sorted(members))``. Edges + members are sorted for deterministic
output (the bridge keeps insertion order; sorting is content-equivalent and SOC 2-reproducible).
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from trax_io_feature_store.glue._common import (
    append_feature_group,
    disable_ansi_mode,
    iceberg_table_identifier,
    load_manifest,
    nonblank,
    read_artifacts,
    select_artifacts,
    validate_manifest_identity,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import DataFrame

LOG = logging.getLogger("trax_io.glue.interchangeable_graph")

INTERCHANGEABLE_GRAPH_COLUMNS: tuple[str, ...] = (
    "pn",
    "group_id",
    "members",
    "edges",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_DOMAIN: frozenset[str] = frozenset({"part_chain_details"})
_ICEBERG_TABLE = "glue_catalog.trax_io.interchangeable_graph"


def select_interchangeable_graph_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return select_artifacts(manifest, _DOMAIN)


def transform_to_interchangeable_graph(
    df: DataFrame, *, tenant_id: str, extract_date: date, manifest_sha256: str
) -> DataFrame:
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    a = F.col("HostPartID").cast(T.StringType())
    b = F.col("HostChainParentID").cast(T.StringType())
    one_way = F.when(
        F.trim(F.coalesce(F.col("RelationType").cast(T.StringType()), F.lit("0"))) == "1",
        F.lit(True),
    ).otherwise(F.lit(False))

    pairs = (
        df.filter(nonblank(a) & nonblank(b) & (a != b))
        .select(a.alias("a"), b.alias("b"), one_way.alias("one_way"))
        .withColumn(
            "edge",
            F.struct(
                F.col("a").alias("from_pn"),
                F.col("b").alias("to_pn"),
                F.col("one_way").alias("one_way"),
            ),
        )
    )
    # Each edge belongs to BOTH endpoints: explode head over {a, b}, carrying the edge + members.
    heads = pairs.select(
        F.explode(F.array(F.col("a"), F.col("b"))).alias("head"),
        F.col("a"),
        F.col("b"),
        F.col("edge"),
    )

    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    grouped = (
        heads.groupBy("head")
        .agg(
            F.sort_array(F.collect_list("edge")).alias("edges"),
            F.array_sort(
                F.array_distinct(F.flatten(F.collect_list(F.array("a", "b"))))
            ).alias("members"),
        )
        .withColumn("pn", F.col("head"))
        .withColumn("group_id", F.concat_ws("+", F.col("members")))
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn("ingested_at", F.lit(ingested_at).cast(T.TimestampType()))
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn("extract_date", F.lit(extract_date).cast(T.DateType()))
    )
    return grouped.select(*INTERCHANGEABLE_GRAPH_COLUMNS)


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
    job.init(f"interchangeable-graph-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    validate_manifest_identity(
        manifest,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
    )
    artifacts = select_interchangeable_graph_artifacts(manifest)
    if not artifacts:
        LOG.warning("no succeeded part_chain_details artifact in manifest; nothing to do")
        job.commit()
        return

    feature_df = transform_to_interchangeable_graph(
        read_artifacts(spark, artifacts),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    append_feature_group(
        feature_df,
        target_table=iceberg_table_identifier(
            args,
            "interchangeable_graph",
        ),
        status_table=iceberg_table_identifier(args, "feature_batch_status"),
        feature_group="interchangeable_graph",
        run_id=str(manifest.get("run_id") or ""),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
