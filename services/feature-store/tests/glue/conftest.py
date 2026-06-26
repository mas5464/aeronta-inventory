"""Pytest fixtures for the Glue-job test suite.

PySpark is an optional dev dep. When it (or its Java runtime) is not
available locally, Spark-requiring tests are skipped rather than failing
collection.
"""

from __future__ import annotations

import contextlib

import pytest


@pytest.fixture(scope="session")
def spark():
    """Session-scoped local SparkSession.

    Skips (with a clear reason) if pyspark is not importable or if the
    JVM cannot start (e.g., no Java on PATH in this environment).
    """
    try:
        from pyspark.sql import SparkSession
    except Exception as exc:  # pragma: no cover -- env-specific
        pytest.skip(f"pyspark not importable: {exc}")

    try:
        session = (
            SparkSession.builder.master("local[1]")
            .appName("trax-io-glue-tests")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .getOrCreate()
        )
    except Exception as exc:  # pragma: no cover -- env-specific
        pytest.skip(f"could not start local SparkSession (Java missing?): {exc}")

    yield session
    with contextlib.suppress(Exception):
        session.stop()
