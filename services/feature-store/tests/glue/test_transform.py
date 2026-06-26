"""End-to-end transform test using a local SparkSession.

Skipped when pyspark / Java are not available (see conftest).
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.demand_history_job import (  # noqa: E402
    DEMAND_HISTORY_COLUMNS,
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
        df, tenant_id="aircanada", extract_date=date(2026, 4, 16), manifest_sha256="sha"
    ).collect()

    by_key = {(r["pn"], r["location"], r["period_start"]): r for r in out}

    a_row = by_key[("PN-A", "LOC-1", date(2026, 4, 10))]
    assert a_row["removals"] == 2
    assert a_row["issues"] == 5

    b_row = by_key[("PN-B", "LOC-2", date(2026, 4, 11))]
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
        assert r["bucket"] == "day"
        # Interchange rollup is deliberately deferred; column must be null.
        assert r["interchange_group_id"] is None
        assert r["ingested_at"] is not None
