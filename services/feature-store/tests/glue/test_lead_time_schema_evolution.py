from __future__ import annotations

from trax_io_feature_store.glue.lead_time_distribution_job import (
    _ICEBERG_TABLE,
    ensure_lead_time_schema,
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


def test_retained_table_gets_all_supply_cycle_provenance_columns() -> None:
    spark = _Spark(["pn", "vendor", "condition", "n_observations"])

    ensure_lead_time_schema(spark)

    assert spark.statements == [
        f"DESCRIBE TABLE {_ICEBERG_TABLE}",
        (
            f"ALTER TABLE {_ICEBERG_TABLE} ADD COLUMNS "
            "(observed_cycle_days array<int>, evidence_status string, "
            "source string, grouping_level string, confidence string, "
            "data_cutoff date, model_version string, proxy_definition string, "
            "classification_source string)"
        ),
    ]


def test_lead_time_schema_evolution_is_idempotent() -> None:
    spark = _Spark(
        [
            "observed_cycle_days",
            "evidence_status",
            "source",
            "grouping_level",
            "confidence",
            "data_cutoff",
            "model_version",
            "proxy_definition",
            "classification_source",
        ]
    )

    ensure_lead_time_schema(spark)

    assert spark.statements == [f"DESCRIBE TABLE {_ICEBERG_TABLE}"]
