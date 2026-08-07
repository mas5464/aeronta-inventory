"""A succeeded artifact with null required demand fields must fail closed."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.demand_history_job import (  # noqa: E402
    transform_to_feature_group,
)


def test_null_host_part_id_fails_the_succeeded_artifact(spark):
    rows = [
        {
            "HostPartID": "PN-OK",
            "HostLocID": "LOC-1",
            "HistoryBegDate": "2026-04-10T10:00:00",
            "HistoryAmount": 1,
            "source_domain": "demand_history_rotables",
        },
        {
            "HostPartID": None,
            "HostLocID": "LOC-1",
            "HistoryBegDate": "2026-04-10T10:00:00",
            "HistoryAmount": 1,
            "source_domain": "demand_history_rotables",
        },
    ]
    df = spark.createDataFrame(rows)
    with pytest.raises(ValueError, match="invalid identity/date/quantity"):
        transform_to_feature_group(
            df, tenant_id="t", extract_date=date(2026, 4, 16), manifest_sha256="s"
        )


def test_null_history_beg_date_fails_the_succeeded_artifact(spark):
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
            "HistoryBegDate": None,
            "HistoryAmount": 1,
            "source_domain": "demand_history_expendables",
        },
    ]
    df = spark.createDataFrame(rows)
    with pytest.raises(ValueError, match="invalid identity/date/quantity"):
        transform_to_feature_group(
            df, tenant_id="t", extract_date=date(2026, 4, 16), manifest_sha256="s"
        )
