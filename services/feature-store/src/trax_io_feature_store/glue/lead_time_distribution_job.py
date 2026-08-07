"""Materialize independent procurement (NEW) and repair (REP) supply cycles.

``pn_vendor_price`` contributes configured promises. ``order_plan_closed_orders``
contributes observed ``ActualRcvDate - PlanOrderDate`` cycles. The output mirrors
the canonical extract loader at two grains: vendor/condition and the real
part/condition ``DEFAULT`` aggregate. Configured-only rows are degenerate
promises; the job never invents percentile spread or blends repair into
procurement.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from trax_io_feature_store.glue._common import (
    append_feature_group,
    disable_ansi_mode,
    finite_double,
    iceberg_table_identifier,
    load_manifest,
    nonblank,
    parse_extract_date,
    read_artifacts,
    select_artifacts,
    validate_manifest_identity,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from pyspark.sql import DataFrame

LOG = logging.getLogger("trax_io.glue.lead_time_distribution")

LEAD_TIME_COLUMNS: tuple[str, ...] = (
    "pn",
    "vendor",
    "condition",
    "promised_lead_days",
    "realized_mean_days",
    "realized_p50_days",
    "realized_p90_days",
    "realized_p99_days",
    "promised_vs_actual_delta_mean",
    "n_observations",
    "observed_cycle_days",
    "evidence_status",
    "source",
    "grouping_level",
    "confidence",
    "data_cutoff",
    "model_version",
    "proxy_definition",
    "classification_source",
    "manifest_sha256",
    "ingested_at",
    "tenant_id",
    "extract_date",
)

_PRICE_DOMAIN: frozenset[str] = frozenset({"pn_vendor_price"})
_CLOSED_DOMAIN: frozenset[str] = frozenset({"order_plan_closed_orders"})
_ICEBERG_TABLE = "glue_catalog.trax_io.lead_time_distribution"
_CANONICAL_VENDOR = "DEFAULT"
_TRUTHY = ("Y", "YES", "TRUE", "1")
_MODEL_VERSION = "supply-cycle-v2"
_EVOLVED_LEAD_TIME_COLUMNS: tuple[tuple[str, str], ...] = (
    ("observed_cycle_days", "array<int>"),
    ("evidence_status", "string"),
    ("source", "string"),
    ("grouping_level", "string"),
    ("confidence", "string"),
    ("data_cutoff", "date"),
    ("model_version", "string"),
    ("proxy_definition", "string"),
    ("classification_source", "string"),
)


def select_lead_time_artifacts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Price artifacts only (the row-driving source); closed orders are optional enrichment."""
    return select_artifacts(manifest, _PRICE_DOMAIN)


def _optional_col(df: DataFrame, name: str):
    """Return a case-insensitive source column, or a typed-later null literal."""

    from pyspark.sql import functions as F  # noqa: N812

    actual = next((column for column in df.columns if column.lower() == name.lower()), None)
    return F.col(actual) if actual is not None else F.lit(None)


def _normalized_price_rows(price_df: DataFrame) -> DataFrame:
    """Classify valid price identities into explicit NEW/REP configuration rows."""

    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    pn_raw = _optional_col(price_df, "HostPartID").cast(T.StringType())
    vendor_raw = _optional_col(price_df, "HostVendorLocID").cast(T.StringType())
    explicit = F.upper(
        F.trim(
            F.coalesce(
                _optional_col(price_df, "OrderTypeID").cast(T.StringType()),
                F.lit(""),
            )
        )
    )
    condition = F.upper(
        F.trim(
            F.coalesce(
                _optional_col(price_df, "Condition").cast(T.StringType()),
                F.lit(""),
            )
        )
    )
    lane = (
        F.when(
            explicit != F.lit(""),
            F.when(explicit == F.lit("PO"), F.lit("NEW")).when(
                explicit == F.lit("RO"),
                F.lit("REP"),
            ),
        )
        .when(condition.isin("REP", "RO"), F.lit("REP"))
        .when(
            condition.isin("", "NEW", "SV", "OH", "AR", "USED", "PO"),
            F.lit("NEW"),
        )
    )
    classification_source = (
        F.when(explicit != F.lit(""), F.lit("explicit_order_type"))
        .when(condition != F.lit(""), F.lit("configured_condition"))
        .otherwise(F.lit("legacy_default_new"))
    )
    processing_raw = _optional_col(price_df, "ProcessingLength")
    processing = processing_raw.cast(T.DoubleType())
    promised = F.when(
        finite_double(processing_raw) & (processing > F.lit(0.0)),
        processing,
    )
    price_raw = _optional_col(price_df, "Price")
    price = price_raw.cast(T.DoubleType())
    price_sort = F.when(finite_double(price_raw), price).otherwise(
        F.lit(float("inf"))
    )
    preferred = F.upper(
        F.trim(
            F.coalesce(
                _optional_col(price_df, "Preferred").cast(T.StringType()),
                F.lit(""),
            )
        )
    )
    return (
        price_df.select(
            F.trim(pn_raw).alias("pn"),
            F.when(nonblank(vendor_raw), F.trim(vendor_raw))
            .otherwise(F.lit(_CANONICAL_VENDOR))
            .alias("vendor"),
            lane.alias("condition"),
            promised.alias("promised_lead_days"),
            classification_source.alias("price_classification_source"),
            F.when(preferred.isin(*_TRUTHY), F.lit(0))
            .otherwise(F.lit(1))
            .alias("_preferred_rank"),
            price_sort.alias("_price_rank"),
        )
        .filter(nonblank(F.col("pn")) & F.col("condition").isNotNull())
    )


def _best_configured_rows(
    price_rows: DataFrame,
    *,
    by_vendor: bool,
) -> DataFrame:
    """Select the canonical configured promise for each output grain."""

    from pyspark.sql import Window
    from pyspark.sql import functions as F  # noqa: N812

    keys = ["pn", "vendor", "condition"] if by_vendor else ["pn", "condition"]
    ordering = [
        F.col("_preferred_rank").asc(),
        F.col("_price_rank").asc(),
        F.col("vendor").asc(),
    ]
    return (
        price_rows.withColumn(
            "_rn",
            F.row_number().over(Window.partitionBy(*keys).orderBy(*ordering)),
        )
        .filter(F.col("_rn") == 1)
        .select(
            *keys,
            "promised_lead_days",
            "price_classification_source",
        )
    )


def _closed_cycle_rows(closed_df: DataFrame) -> DataFrame:
    """Classify observed closed orders and retain actual-receipt cycle evidence."""

    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    pn_raw = _optional_col(closed_df, "HostPartID").cast(T.StringType())
    vendor_raw = _optional_col(closed_df, "HostVendorLocID").cast(T.StringType())
    explicit = F.upper(
        F.trim(
            F.coalesce(
                _optional_col(closed_df, "OrderTypeID").cast(T.StringType()),
                F.lit(""),
            )
        )
    )

    def legacy_prefix(column_name: str):
        value = F.upper(
            F.trim(
                F.coalesce(
                    _optional_col(closed_df, column_name).cast(T.StringType()),
                    F.lit(""),
                )
            )
        )
        return (
            F.when(value.rlike(r"^PO[_/-]"), F.lit("PO"))
            .when(value.rlike(r"^RO[_/-]"), F.lit("RO"))
        )

    host_prefix = legacy_prefix("HostOrderID")
    order_prefix = legacy_prefix("OrderID")
    legacy = (
        F.when(host_prefix.isNull(), order_prefix)
        .when(order_prefix.isNull(), host_prefix)
        .when(host_prefix == order_prefix, host_prefix)
    )
    lane = (
        F.when(
            explicit != F.lit(""),
            F.when(explicit == F.lit("PO"), F.lit("NEW")).when(
                explicit == F.lit("RO"),
                F.lit("REP"),
            ),
        )
        .when(legacy == F.lit("PO"), F.lit("NEW"))
        .when(legacy == F.lit("RO"), F.lit("REP"))
    )
    classification_source = (
        F.when(
            (explicit != F.lit("")) & lane.isNotNull(),
            F.lit("explicit_order_type"),
        )
        .when(legacy.isNotNull(), F.lit("legacy_order_id_prefix"))
    )
    normalized = closed_df.select(
        F.trim(pn_raw).alias("pn"),
        F.when(nonblank(vendor_raw), F.trim(vendor_raw))
        .otherwise(F.lit(_CANONICAL_VENDOR))
        .alias("vendor"),
        lane.alias("condition"),
        classification_source.alias("cycle_classification_source"),
        parse_extract_date(_optional_col(closed_df, "PlanOrderDate")).alias(
            "ordered"
        ),
        parse_extract_date(_optional_col(closed_df, "ActualRcvDate")).alias(
            "received"
        ),
    )
    return (
        normalized.filter(
            nonblank(F.col("pn"))
            & F.col("condition").isNotNull()
            & F.col("ordered").isNotNull()
            & F.col("received").isNotNull()
            & (F.col("received") >= F.col("ordered"))
        )
        .withColumn("days", F.datediff(F.col("received"), F.col("ordered")))
    )


def _aggregate_cycles(
    cycle_rows: DataFrame,
    *,
    by_vendor: bool,
) -> DataFrame:
    """Aggregate observed cycles without interpolating the canonical quantiles."""

    from pyspark.sql import functions as F  # noqa: N812

    keys = ["pn", "vendor", "condition"] if by_vendor else ["pn", "condition"]
    return cycle_rows.groupBy(*keys).agg(
        F.sort_array(F.collect_list("days")).alias("sorted_days"),
        F.count(F.lit(1)).alias("n"),
        F.avg("days").alias("mean_realized"),
        F.max("received").alias("observed_data_cutoff"),
        F.when(
            F.max(
                F.when(
                    F.col("cycle_classification_source")
                    == F.lit("legacy_order_id_prefix"),
                    F.lit(1),
                ).otherwise(F.lit(0))
            )
            > F.lit(0),
            F.lit("legacy_order_id_prefix"),
        )
        .otherwise(F.lit("explicit_order_type"))
        .alias("cycle_classification_source"),
    )


def _distribution_rows(
    configured: DataFrame,
    observed: DataFrame | None,
    *,
    by_vendor: bool,
    tenant_id: str,
    extract_date: date,
    manifest_sha256: str,
    ingested_at: datetime,
) -> DataFrame:
    """Full-outer configured/observed evidence at one supply-cycle grain."""

    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    keys = ["pn", "vendor", "condition"] if by_vendor else ["pn", "condition"]
    if observed is None:
        joined = (
            configured.withColumn(
                "sorted_days",
                F.lit(None).cast(T.ArrayType(T.IntegerType())),
            )
            .withColumn("n", F.lit(None).cast(T.LongType()))
            .withColumn("mean_realized", F.lit(None).cast(T.DoubleType()))
            .withColumn("observed_data_cutoff", F.lit(None).cast(T.DateType()))
            .withColumn(
                "cycle_classification_source",
                F.lit(None).cast(T.StringType()),
            )
        )
    else:
        joined = configured.join(observed, on=keys, how="full")
    if not by_vendor:
        joined = joined.withColumn("vendor", F.lit(_CANONICAL_VENDOR))

    n = F.col("n")
    has_observations = n.isNotNull() & (n > F.lit(0))
    sorted_days = F.col("sorted_days")
    promised = F.col("promised_lead_days")
    n_minus_one = n - F.lit(1)
    idx50 = (F.floor(n / F.lit(2)) + F.lit(1)).cast(T.IntegerType())
    idx90 = (
        F.least(
            n_minus_one,
            F.bround(F.lit(0.9) * n_minus_one).cast(T.IntegerType()),
        )
        + F.lit(1)
    ).cast(T.IntegerType())
    idx99 = (
        F.least(
            n_minus_one,
            F.bround(F.lit(0.99) * n_minus_one).cast(T.IntegerType()),
        )
        + F.lit(1)
    ).cast(T.IntegerType())
    p50 = F.element_at(sorted_days, idx50).cast(T.DoubleType())
    p90 = F.element_at(sorted_days, idx90).cast(T.DoubleType())
    p99 = F.element_at(sorted_days, idx99).cast(T.DoubleType())
    observed_mean = F.col("mean_realized")

    return (
        joined.filter(has_observations | promised.isNotNull())
        .withColumn(
            "realized_mean_days",
            F.when(has_observations, observed_mean).otherwise(promised),
        )
        .withColumn(
            "realized_p50_days",
            F.when(has_observations, p50).otherwise(promised),
        )
        .withColumn(
            "realized_p90_days",
            F.when(has_observations, p90).otherwise(promised),
        )
        .withColumn(
            "realized_p99_days",
            F.when(has_observations, p99).otherwise(promised),
        )
        .withColumn(
            "promised_vs_actual_delta_mean",
            F.when(
                has_observations & promised.isNotNull(),
                observed_mean - promised,
            ).otherwise(F.lit(None).cast(T.DoubleType())),
        )
        .withColumn(
            "n_observations",
            F.coalesce(n, F.lit(0)).cast(T.IntegerType()),
        )
        .withColumn(
            "observed_cycle_days",
            F.when(has_observations, sorted_days).otherwise(
                F.array().cast(T.ArrayType(T.IntegerType()))
            ),
        )
        .withColumn(
            "evidence_status",
            F.when(has_observations, F.lit("observed")).otherwise(
                F.lit("configured_fallback")
            ),
        )
        .withColumn(
            "source",
            F.when(has_observations, F.lit("order_plan_closed_orders")).otherwise(
                F.lit("pn_vendor_price")
            ),
        )
        .withColumn(
            "grouping_level",
            F.lit("part_vendor_condition" if by_vendor else "part_condition"),
        )
        .withColumn(
            "confidence",
            F.when(n >= F.lit(30), F.lit("high"))
            .when(n >= F.lit(10), F.lit("medium"))
            .otherwise(F.lit("low")),
        )
        .withColumn(
            "data_cutoff",
            F.when(has_observations, F.col("observed_data_cutoff")).otherwise(
                F.lit(extract_date).cast(T.DateType())
            ),
        )
        .withColumn("model_version", F.lit(_MODEL_VERSION))
        .withColumn(
            "proxy_definition",
            F.when(
                F.col("condition") == F.lit("REP"),
                F.when(
                    has_observations,
                    F.lit("order_creation_to_last_receipt"),
                ).otherwise(F.lit("configured_repair_promise")),
            ).otherwise(F.lit(None).cast(T.StringType())),
        )
        .withColumn(
            "classification_source",
            F.when(
                has_observations,
                F.col("cycle_classification_source"),
            ).otherwise(F.col("price_classification_source")),
        )
        .withColumn("manifest_sha256", F.lit(manifest_sha256))
        .withColumn(
            "ingested_at",
            F.lit(ingested_at).cast(T.TimestampType()),
        )
        .withColumn("tenant_id", F.lit(tenant_id))
        .withColumn(
            "extract_date",
            F.lit(extract_date).cast(T.DateType()),
        )
        .select(*LEAD_TIME_COLUMNS)
    )


def transform_to_lead_time(
    price_df: DataFrame | None,
    closed_df: DataFrame | None,
    *,
    tenant_id: str,
    extract_date: date,
    manifest_sha256: str,
) -> DataFrame:
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql import types as T  # noqa: N812

    if price_df is None and closed_df is None:
        raise ValueError("lead-time transform requires price or closed-order evidence")
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    cycle_rows = _closed_cycle_rows(closed_df) if closed_df is not None else None
    if price_df is not None:
        price_rows = _normalized_price_rows(price_df)
    else:
        assert cycle_rows is not None
        price_rows = (
            cycle_rows.select("pn", "vendor", "condition")
            .limit(0)
            .withColumn(
                "promised_lead_days",
                F.lit(None).cast(T.DoubleType()),
            )
            .withColumn(
                "price_classification_source",
                F.lit(None).cast(T.StringType()),
            )
            .withColumn("_preferred_rank", F.lit(0))
            .withColumn("_price_rank", F.lit(0.0))
        )
    vendor_rows = _distribution_rows(
        _best_configured_rows(price_rows, by_vendor=True),
        _aggregate_cycles(cycle_rows, by_vendor=True)
        if cycle_rows is not None
        else None,
        by_vendor=True,
        tenant_id=tenant_id,
        extract_date=extract_date,
        manifest_sha256=manifest_sha256,
        ingested_at=ingested_at,
    ).filter(F.col("vendor") != F.lit(_CANONICAL_VENDOR))
    default_rows = _distribution_rows(
        _best_configured_rows(price_rows, by_vendor=False),
        _aggregate_cycles(cycle_rows, by_vendor=False)
        if cycle_rows is not None
        else None,
        by_vendor=False,
        tenant_id=tenant_id,
        extract_date=extract_date,
        manifest_sha256=manifest_sha256,
        ingested_at=ingested_at,
    )
    return vendor_rows.unionByName(default_rows).select(*LEAD_TIME_COLUMNS)


def ensure_lead_time_schema(
    spark: Any,
    *,
    table: str = _ICEBERG_TABLE,
) -> None:
    """Idempotently add nullable provenance fields to a retained Iceberg table."""

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
        for name, column_type in _EVOLVED_LEAD_TIME_COLUMNS
        if name not in existing
    ]
    if not missing:
        return
    definitions = ", ".join(
        f"{name} {column_type}" for name, column_type in missing
    )
    spark.sql(f"ALTER TABLE {table} ADD COLUMNS ({definitions})")


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
    job.init(f"lead-time-{args['tenant_id']}-{args['extract_date']}", args)

    manifest = load_manifest(spark, args["manifest_s3_uri"])
    validate_manifest_identity(
        manifest,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
    )
    price_artifacts = select_lead_time_artifacts(manifest)
    closed_artifacts = select_artifacts(manifest, _CLOSED_DOMAIN)
    if not price_artifacts and not closed_artifacts:
        LOG.warning(
            "no succeeded price or closed-order artifact in manifest; nothing to do"
        )
        job.commit()
        return

    price_df = read_artifacts(spark, price_artifacts) if price_artifacts else None
    closed_df = read_artifacts(spark, closed_artifacts) if closed_artifacts else None

    feature_df = transform_to_lead_time(
        price_df,
        closed_df,
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    target_table = iceberg_table_identifier(
        args,
        "lead_time_distribution",
    )
    ensure_lead_time_schema(spark, table=target_table)
    append_feature_group(
        feature_df,
        target_table=target_table,
        status_table=iceberg_table_identifier(args, "feature_batch_status"),
        feature_group="lead_time_distribution",
        run_id=str(manifest.get("run_id") or ""),
        tenant_id=args["tenant_id"],
        extract_date=date.fromisoformat(args["extract_date"]),
        manifest_sha256=str(manifest.get("source_sql_sha256") or ""),
    )
    job.commit()


if __name__ == "__main__":  # pragma: no cover -- Glue entrypoint
    main()
