"""stock_position Glue transform — runs against a local SparkSession (skips without Java)."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.stock_position_job import (  # noqa: E402
    STOCK_POSITION_COLUMNS,
    select_stock_position_artifacts,
    transform_to_stock_position,
)


def test_select_stock_position_artifacts() -> None:
    manifest = {
        "artifacts": [
            {"domain": "stock_amount", "status": "succeeded", "s3_uri": "s3://x/stock_amount.json"},
            {"domain": "stock_amount", "status": "failed"},
            {"domain": "part_master", "status": "succeeded"},
        ]
    }
    sel = select_stock_position_artifacts(manifest)
    assert [a["domain"] for a in sel] == ["stock_amount"]


def test_transform_stock_position(spark) -> None:
    rows = [
        # lowercased extract aliases; Spark resolves HostPartID case-insensitively
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "onhandnew": 8, "onhandbad": 2,
         "inrepair": 3, "allocated": 2, "rentalqty": 1, "loanqty": 0},
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "onhandnew": 8, "onhandbad": 2,
         "inrepair": 3, "allocated": 2, "rentalqty": 1, "loanqty": 0},  # duplicate -> deduped
        {"hostpartid": None, "hostlocid": "LOC-1", "onhandnew": 5, "onhandbad": 0,
         "inrepair": 0, "allocated": 0, "rentalqty": 0, "loanqty": 0},  # null pn -> dropped
    ]
    out = transform_to_stock_position(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.columns == list(STOCK_POSITION_COLUMNS)
    recs = out.collect()
    assert len(recs) == 1  # dup deduped, null-pn dropped
    r = recs[0]
    assert (r["pn"], r["location"]) == ("PN-A", "LOC-1")
    assert r["serviceable"] == 8
    assert r["on_hand"] == 13  # OnHandNew + OnHandBad + InRepair = 8+2+3
    assert r["unserviceable_in_repair"] == 3
    assert (r["allocated_reserved"], r["rental"], r["loan"]) == (2, 1, 0)
    assert r["tenant_id"] == "acme" and str(r["extract_date"]) == "2026-04-01"
    assert r["manifest_sha256"] == "sha123"
