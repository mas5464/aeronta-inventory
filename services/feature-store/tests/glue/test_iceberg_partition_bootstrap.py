"""Executable Iceberg bootstrap verifies metadata before every append."""

from __future__ import annotations

import pytest

from trax_io_feature_store.glue._common import (
    append_iceberg,
    ensure_iceberg_partition_spec,
)


class _Rows:
    def __init__(self, value: object):
        self._value = value

    def collect(self) -> list[tuple[object]]:
        return [(self._value,)]


class _Catalog:
    def __init__(self, spark: _Spark) -> None:
        self._spark = spark

    def tableExists(self, _table: str) -> bool:  # noqa: N802 - Spark API parity
        return self._spark.exists


class _Spark:
    def __init__(self, fields: tuple[str, ...] | None) -> None:
        self.exists = fields is not None
        self.fields = () if fields is None else fields
        self.catalog = _Catalog(self)
        self.statements: list[str] = []

    def _ddl(self) -> str:
        partition = (
            f" PARTITIONED BY ({', '.join(self.fields)})"
            if self.fields
            else ""
        )
        return f"CREATE TABLE glue_catalog.db.raw_stock (pn string){partition} USING iceberg"

    def sql(self, statement: str) -> _Rows:
        self.statements.append(statement)
        if statement.startswith("SHOW CREATE TABLE"):
            return _Rows(self._ddl())
        if statement.startswith("ALTER TABLE") and "ADD PARTITION FIELD" in statement:
            self.fields = (*self.fields, statement.rsplit(" ", 1)[-1])
            return _Rows(None)
        raise AssertionError(f"unexpected SQL: {statement}")


class _Writer:
    def __init__(self, frame: _Frame, table: str) -> None:
        self.frame = frame
        self.table = table

    def using(self, provider: str) -> _Writer:
        self.frame.created_using = provider
        return self

    def partitionedBy(self, *columns: object) -> _Writer:  # noqa: N802
        self.frame.created_partition_count = len(columns)
        return self

    def tableProperty(self, key: str, value: str) -> _Writer:  # noqa: N802
        self.frame.table_properties[key] = value
        return self

    def create(self) -> None:
        self.frame.sparkSession.exists = True
        self.frame.sparkSession.fields = ("tenant_id", "extract_date")
        self.frame.created = True

    def option(self, key: str, value: str) -> _Writer:
        self.frame.write_options[key] = value
        return self

    def append(self) -> None:
        self.frame.appended = True


class _Frame:
    def __init__(self, spark: _Spark) -> None:
        self.sparkSession = spark
        self.created = False
        self.created_using: str | None = None
        self.created_partition_count = 0
        self.table_properties: dict[str, str] = {}
        self.write_options: dict[str, str] = {}
        self.appended = False

    def limit(self, rows: int) -> _Frame:
        assert rows == 0
        return self

    def writeTo(self, table: str) -> _Writer:  # noqa: N802 - Spark API parity
        return _Writer(self, table)


def test_missing_table_is_created_partitioned_then_appended(monkeypatch) -> None:
    # The fake writer only needs partition-column cardinality, so avoid creating
    # real Spark Columns while preserving the production writer call contract.
    monkeypatch.setattr(
        "pyspark.sql.functions.col",
        lambda name: name,
    )
    frame = _Frame(_Spark(None))

    append_iceberg(frame, "glue_catalog.db.raw_stock")

    assert frame.created is True
    assert frame.created_using == "iceberg"
    assert frame.created_partition_count == 2
    assert frame.table_properties == {"format-version": "2"}
    assert frame.appended is True
    assert frame.write_options == {"write-format": "parquet"}


def test_unpartitioned_catalog_shell_is_evolved_before_append() -> None:
    spark = _Spark(())
    frame = _Frame(spark)

    append_iceberg(frame, "glue_catalog.db.raw_stock")

    assert spark.fields == ("tenant_id", "extract_date")
    assert [
        statement
        for statement in spark.statements
        if statement.startswith("ALTER TABLE")
    ] == [
        "ALTER TABLE glue_catalog.db.raw_stock ADD PARTITION FIELD tenant_id",
        "ALTER TABLE glue_catalog.db.raw_stock ADD PARTITION FIELD extract_date",
    ]
    assert frame.appended is True


def test_exact_partition_spec_is_verified_without_mutation() -> None:
    spark = _Spark(("tenant_id", "extract_date"))

    ensure_iceberg_partition_spec(
        _Frame(spark),
        "glue_catalog.db.raw_stock",
    )

    assert all(not statement.startswith("ALTER TABLE") for statement in spark.statements)


@pytest.mark.parametrize(
    "fields",
    [
        ("extract_date", "tenant_id"),
        ("tenant_id", "extract_date", "pn"),
        ("bucket(16, tenant_id)", "extract_date"),
    ],
)
def test_unexpected_partition_spec_fails_closed(fields: tuple[str, ...]) -> None:
    with pytest.raises(RuntimeError, match="unsafe Iceberg partition spec"):
        ensure_iceberg_partition_spec(
            _Frame(_Spark(fields)),
            "glue_catalog.db.raw_stock",
        )


@pytest.mark.parametrize(
    "identifier",
    [
        "glue_catalog.db.raw_stock; DROP TABLE other",
        "glue_catalog.db.raw-stock",
        "raw_stock",
    ],
)
def test_table_identifier_is_not_a_sql_injection_surface(identifier: str) -> None:
    with pytest.raises(ValueError, match="unsafe Iceberg table identifier"):
        ensure_iceberg_partition_spec(_Frame(_Spark(())), identifier)
