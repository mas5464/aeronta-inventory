from __future__ import annotations

from trax_io_feature_store.glue.demand_history_job import (
    _ICEBERG_TABLE,
    ensure_demand_history_schema,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class _Spark:
    def __init__(self, existing: list[str]):
        self.existing = existing
        self.statements: list[str] = []

    def sql(self, statement: str):
        self.statements.append(statement)
        if statement.startswith("DESCRIBE"):
            return _Result([{"col_name": name} for name in self.existing])
        return _Result([])


def test_retained_table_gets_all_additive_demand_columns() -> None:
    spark = _Spark(
        [
            "pn",
            "location",
            "bucket",
            "period_start",
            "removals",
            "issues",
        ]
    )

    ensure_demand_history_schema(spark)

    assert spark.statements == [
        f"DESCRIBE TABLE {_ICEBERG_TABLE}",
        (
            f"ALTER TABLE {_ICEBERG_TABLE} ADD COLUMNS "
            "(removal_events int, issue_events int, observation_start date, "
            "observation_end date, event_count_source string)"
        ),
    ]


def test_schema_evolution_is_noop_when_columns_already_exist() -> None:
    spark = _Spark(
        [
            "removal_events",
            "issue_events",
            "observation_start",
            "observation_end",
            "event_count_source",
        ]
    )

    ensure_demand_history_schema(spark)

    assert spark.statements == [f"DESCRIBE TABLE {_ICEBERG_TABLE}"]
