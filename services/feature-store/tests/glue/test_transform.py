"""End-to-end transform test using a local SparkSession.

Skipped when pyspark / Java are not available (see conftest).
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.demand_history_job import (  # noqa: E402
    DEMAND_HISTORY_COLUMNS,
    read_raw,
    transform_to_feature_group,
)


def _raw_rows(spark):
    """Build a small raw DataFrame with both source domains.

    HistoryAmount is a *string* (as the real extract delivers it); the fixture runs ANSI off
    like Glue 4.0, so this exercises the production string->int cast path.
    """
    rows = [
        # Two rotable removals for PN-A at LOC-1 on the same day -> removals=2
        {
            "HostPartID": "PN-A",
            "HostLocID": "LOC-1",
            "HistoryBegDate": "2026-04-10T08:15:00",
            "HistoryAmount": "1",
            "source_domain": "demand_history_rotables",
        },
        {
            "HostPartID": "PN-A",
            "HostLocID": "LOC-1",
            "HistoryBegDate": "2026-04-10T14:02:00",
            "HistoryAmount": "1",
            "source_domain": "demand_history_rotables",
        },
        # Expendable issue for the SAME (pn, location, day) -> issues=5
        {
            "HostPartID": "PN-A",
            "HostLocID": "LOC-1",
            "HistoryBegDate": "2026-04-10T09:30:00",
            "HistoryAmount": "5",
            "source_domain": "demand_history_expendables",
        },
        # Different PN, expendable only -> removals=0, issues=3
        {
            "HostPartID": "PN-B",
            "HostLocID": "LOC-2",
            "HistoryBegDate": "2026-04-11T12:00:00",
            "HistoryAmount": "3",
            "source_domain": "demand_history_expendables",
        },
    ]
    return spark.createDataFrame(rows)


def test_output_schema_matches_iceberg_target_columns_in_order(spark):
    df = _raw_rows(spark)
    out = transform_to_feature_group(
        df, tenant_id="aircanada", extract_date=date(2026, 4, 16), manifest_sha256="sha"
    )
    assert tuple(out.columns) == DEMAND_HISTORY_COLUMNS


def test_aggregation_splits_removals_and_issues_by_source(spark):
    df = _raw_rows(spark)
    out = transform_to_feature_group(
        df,
        tenant_id="aircanada",
        extract_date=date(2026, 4, 16),
        manifest_sha256="sha",
        observation_start=date(2023, 4, 16),
        observation_end=date(2026, 4, 16),
    ).collect()

    by_key = {(r["pn"], r["location"], r["period_start"]): r for r in out}

    a_row = by_key[("PN-A", "LOC-1", date(2026, 4, 1))]
    assert a_row["removals"] == 2
    assert a_row["issues"] == 5
    assert a_row["removal_events"] == 2
    assert a_row["issue_events"] == 1
    assert a_row["observation_start"] == date(2023, 4, 16)
    assert a_row["observation_end"] == date(2026, 4, 16)
    assert a_row["event_count_source"] == "observed"

    b_row = by_key[("PN-B", "LOC-2", date(2026, 4, 1))]
    assert b_row["removals"] == 0
    assert b_row["issues"] == 3


def test_history_amount_rounds_per_row_before_sum(spark):
    # The bridge does ``_i(historyamount)`` per row then sums; summing the raw strings then
    # casting would diverge on fractional qtys. Two "2.5" issues must give 4 (bround:2+2), not 5.
    rows = [
        {"HostPartID": "PN-C", "HostLocID": "LOC-9", "HistoryBegDate": "2026-04-12T01:00:00",
         "HistoryAmount": "2.5", "source_domain": "demand_history_expendables"},
        {"HostPartID": "PN-C", "HostLocID": "LOC-9", "HistoryBegDate": "2026-04-12T02:00:00",
         "HistoryAmount": "2.5", "source_domain": "demand_history_expendables"},
    ]
    out = transform_to_feature_group(
        spark.createDataFrame(rows), tenant_id="ac", extract_date=date(2026, 4, 12),
        manifest_sha256="sha",
    ).collect()
    assert len(out) == 1
    assert out[0]["issues"] == 4  # round-per-row (2+2), NOT sum-then-trunc (5.0 -> 5)
    assert out[0]["removals"] == 0


def test_metadata_columns_populated(spark):
    df = _raw_rows(spark)
    out = transform_to_feature_group(
        df,
        tenant_id="aircanada",
        extract_date=date(2026, 4, 16),
        manifest_sha256="abcdef",
    ).collect()

    for r in out:
        assert r["tenant_id"] == "aircanada"
        assert r["extract_date"] == date(2026, 4, 16)
        assert r["source"] == "nightly-extract"
        assert r["manifest_sha256"] == "abcdef"
        assert r["bucket"] == "month"
        # Interchange rollup is deliberately deferred; column must be null.
        assert r["interchange_group_id"] is None
        assert r["ingested_at"] is not None


def test_configured_zero_demand_stock_key_gets_one_zero_marker(spark) -> None:
    planning_keys = spark.createDataFrame(
        [
            {"HostPartID": "PN-A", "HostLocID": "LOC-1"},
            {"HostPartID": "PN-ZERO", "HostLocID": "LOC-9"},
        ]
    )
    out = transform_to_feature_group(
        _raw_rows(spark),
        tenant_id="aircanada",
        extract_date=date(2026, 4, 16),
        manifest_sha256="sha",
        observation_start=date(2023, 4, 16),
        observation_end=date(2026, 4, 16),
        planning_keys=planning_keys,
    ).collect()

    zero_rows = [row for row in out if row["pn"] == "PN-ZERO"]
    assert len(zero_rows) == 1
    marker = zero_rows[0]
    assert marker["location"] == "LOC-9"
    assert marker["period_start"] == date(2023, 4, 16)
    assert (
        marker["removals"],
        marker["issues"],
        marker["removal_events"],
        marker["issue_events"],
    ) == (0, 0, 0, 0)
    # A key with real demand is not given an extra marker row.
    assert len([row for row in out if row["pn"] == "PN-A"]) == 1


def test_two_successful_empty_artifacts_emit_markers_for_every_planning_key(
    spark,
) -> None:
    class EmptyArtifactReader:
        """Stand in for two valid ``[]`` files without touching Hadoop's local FS."""

        def __init__(self, session):
            self._session = session
            self._schema = None

        def schema(self, schema):
            self._schema = schema
            return self

        def option(self, _key, _value):
            return self

        def json(self, _uri):
            assert self._schema is not None
            return self._session.createDataFrame([], self._schema)

    class EmptyArtifactSpark:
        def __init__(self, session):
            self.read = EmptyArtifactReader(session)

    raw = read_raw(
        EmptyArtifactSpark(spark),
        [
            {
                "domain": "demand_history_rotables",
                "s3_uri": "s3://landing/demand_history_rotables.json",
            },
            {
                "domain": "demand_history_expendables",
                "s3_uri": "s3://landing/demand_history_expendables.json",
            },
        ],
    )
    planning_keys = spark.createDataFrame(
        [
            {"HostPartID": "PN-ZERO-1", "HostLocID": "LOC-1"},
            {"HostPartID": "PN-ZERO-2", "HostLocID": "LOC-2"},
        ]
    )

    out = transform_to_feature_group(
        raw,
        tenant_id="aircanada",
        extract_date=date(2026, 4, 16),
        manifest_sha256="sha",
        observation_start=date(2023, 4, 16),
        observation_end=date(2026, 4, 16),
        planning_keys=planning_keys,
    ).collect()

    assert {
        (
            row["pn"],
            row["location"],
            row["removals"],
            row["issues"],
            row["removal_events"],
            row["issue_events"],
        )
        for row in out
    } == {
        ("PN-ZERO-1", "LOC-1", 0, 0, 0, 0),
        ("PN-ZERO-2", "LOC-2", 0, 0, 0, 0),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("HostPartID", None),
        ("HostLocID", None),
        ("HistoryBegDate", "not-a-date"),
        ("HistoryAmount", "not-a-number"),
        ("HistoryAmount", "Infinity"),
        ("HistoryAmount", "-Infinity"),
    ],
)
def test_malformed_succeeded_demand_row_fails_before_zero_markers(
    spark,
    field,
    value,
) -> None:
    valid_row = {
        "HostPartID": "PN-A",
        "HostLocID": "LOC-1",
        "HistoryBegDate": "2026-04-10",
        "HistoryAmount": "1",
        "source_domain": "demand_history_rotables",
    }
    row = {**valid_row}
    row[field] = value
    planning_keys = spark.createDataFrame(
        [{"HostPartID": "PN-A", "HostLocID": "LOC-1"}]
    )

    with pytest.raises(ValueError, match="invalid identity/date/quantity"):
        transform_to_feature_group(
            spark.createDataFrame([row, valid_row]),
            tenant_id="aircanada",
            extract_date=date(2026, 4, 16),
            manifest_sha256="sha",
            observation_start=date(2023, 4, 16),
            observation_end=date(2026, 4, 16),
            planning_keys=planning_keys,
        )


def test_pooled_key_universe_uses_only_planning_active_policy_locations(spark) -> None:
    planning_keys = spark.createDataFrame(
        [
            {
                "HostPartID": "PN-A",
                "HostLocID": "PHYSICAL",
                "rop": None,
                "stockmax": None,
            },
            {
                "HostPartID": "PN-PLAN",
                "HostLocID": "LP",
                "rop": "5",
                "stockmax": "20",
            },
            {
                "HostPartID": "PN-ZERO-POLICY",
                "HostLocID": "FAT",
                "rop": "0",
                "stockmax": "0",
            },
        ]
    )

    out = transform_to_feature_group(
        _raw_rows(spark),
        tenant_id="aircanada",
        extract_date=date(2026, 4, 16),
        manifest_sha256="sha",
        observation_start=date(2023, 4, 16),
        observation_end=date(2026, 4, 16),
        planning_keys=planning_keys,
        planning_active_only=True,
    ).collect()

    markers = {(row["pn"], row["location"]) for row in out if row["removals"] == 0
               and row["issues"] == 0}
    assert ("PN-PLAN", "LP") in markers
    assert ("PN-ZERO-POLICY", "FAT") not in markers
    assert ("PN-A", "PHYSICAL") not in markers
