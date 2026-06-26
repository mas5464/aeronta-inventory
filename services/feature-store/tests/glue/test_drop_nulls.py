"""Rows with null HostPartID / HistoryBegDate must be dropped by the transform."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.demand_history_job import (  # noqa: E402
    transform_to_feature_group,
)


def test_null_host_part_id_row_is_dropped(spark):
    rows = [
        {
            "HostPartID": "PN-OK",
            "HostLocID": "LOC-1",
            "HistoryBegDate": "2026-04-10T10:00:00",
            "HistoryAmount": 1,
            "source_domain": "demand_history_rotables",
        },
        {
            "HostPartID": None,  # <-- must be dropped
            "HostLocID": "LOC-1",
            "HistoryBegDate": "2026-04-10T10:00:00",
            "HistoryAmount": 1,
            "source_domain": "demand_history_rotables",
        },
    ]
    df = spark.createDataFrame(rows)
    out = transform_to_feature_group(
        df, tenant_id="t", extract_date=date(2026, 4, 16), manifest_sha256="s"
    ).collect()
    pns = {r["pn"] for r in out}
    assert pns == {"PN-OK"}


def test_null_history_beg_date_row_is_dropped(spark):
    rows = [
        {
            "HostPartID": "PN-OK",
            "HostLocID": "LOC-1",
            "HistoryBegDate": "2026-04-10T10:00:00",
            "HistoryAmount": 1,
            "source_domain": "demand_history_expendables",
        },
        {
            "HostPartID": "PN-NULLDATE",
            "HostLocID": "LOC-1",
            "HistoryBegDate": None,  # <-- must be dropped
            "HistoryAmount": 1,
            "source_domain": "demand_history_expendables",
        },
    ]
    df = spark.createDataFrame(rows)
    out = transform_to_feature_group(
        df, tenant_id="t", extract_date=date(2026, 4, 16), manifest_sha256="s"
    ).collect()
    pns = {r["pn"] for r in out}
    assert pns == {"PN-OK"}
