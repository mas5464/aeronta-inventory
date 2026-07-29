"""Glue 4 driver job: populate tenant DynamoDB bundles from committed Iceberg.

Unlike the Spark transform modules, this entrypoint intentionally installs and
loads the application read stack (Pydantic + PyIceberg + PyArrow). It runs only
after the extract-run ledger is committed, enumerates the offline stock-backed
key universe, and uses the shared native population composition.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

LOG = logging.getLogger("trax_io.glue.online_population")


def _parse_args(argv: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    it = iter(argv)
    for token in it:
        if token.startswith("--"):
            try:
                out[token[2:]] = next(it)
            except StopIteration as exc:
                raise ValueError(f"missing value for arg {token!r}") from exc
    missing = {"tenant_id", "online_table_name"} - out.keys()
    if missing:
        raise ValueError(f"missing required args: {sorted(missing)}")
    return out


def _population_config(args: dict[str, str]) -> dict[str, str]:
    config = {
        "TRAX_IO_FEATURE_ONLINE_TABLE": args["online_table_name"],
        "TRAX_IO_FEATURE_CATALOG_NAME": args.get("catalog_name", "glue"),
        "TRAX_IO_FEATURE_CATALOG_TYPE": args.get("catalog_type", "glue"),
    }
    optional = {
        "warehouse": "TRAX_IO_FEATURE_WAREHOUSE",
        "catalog_database": "TRAX_IO_FEATURE_NAMESPACE",
        "table_prefix": "TRAX_IO_FEATURE_TABLE_PREFIX",
    }
    for arg_name, env_name in optional.items():
        if arg_name in args:
            config[env_name] = args[arg_name]
    return config


def run_population(
    args: dict[str, str],
    *,
    populate: Callable[..., Any] | None = None,
) -> Any:
    """Run one population pass and fail the Glue run on partial writes."""

    if populate is None:
        from trax_io_feature_store.runtime import populate_native_online_from_env

        populate = populate_native_online_from_env
    demand_window = (
        int(args["demand_window"])
        if "demand_window" in args
        else None
    )
    if demand_window is not None and demand_window <= 0:
        raise ValueError("demand_window must be positive")
    result = populate(
        args["tenant_id"],
        environ=_population_config(args),
        demand_window=demand_window,
    )
    LOG.info(
        "online population tenant=%s keys=%d written=%d skipped_incomplete=%d "
        "failed_oversize=%d failed_writes=%d generation=%s",
        args["tenant_id"],
        len(result.keys),
        result.population.written,
        result.population.skipped_incomplete,
        result.population.failed_oversize,
        result.population.failed_writes,
        result.population.committed_generation,
    )
    failures = (
        result.population.failed_oversize
        + result.population.failed_writes
    )
    if failures:
        raise RuntimeError(
            "online population failed for "
            f"{failures} DynamoDB item(s)"
        )
    return result


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    from awsglue.context import GlueContext  # type: ignore[import-not-found]
    from awsglue.job import Job  # type: ignore[import-not-found]
    from pyspark.context import SparkContext

    glue_context = GlueContext(SparkContext.getOrCreate())
    job = Job(glue_context)
    job.init(f"online-population-{args['tenant_id']}", args)
    run_population(args)
    job.commit()


if __name__ == "__main__":  # pragma: no cover - Glue entrypoint
    main()
