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
        # lowercased extract aliases delivered as strings (real extract shape); Spark resolves
        # HostPartID case-insensitively and (ANSI off) parses the string numerics.
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "onhandnew": "8", "onhandbad": "2",
         "inrepair": "3", "allocated": "2", "rentalqty": "1", "loanqty": "0"},
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "onhandnew": "8", "onhandbad": "2",
         "inrepair": "3", "allocated": "2", "rentalqty": "1", "loanqty": "0"},  # dup -> deduped
    ]
    out = transform_to_stock_position(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.columns == list(STOCK_POSITION_COLUMNS)
    recs = out.collect()
    assert len(recs) == 1  # duplicate deduped
    r = recs[0]
    assert (r["pn"], r["location"]) == ("PN-A", "LOC-1")
    assert r["serviceable"] == 8
    assert r["on_hand"] == 13  # OnHandNew + OnHandBad + InRepair = 8+2+3
    assert r["unserviceable_in_repair"] == 3
    assert (r["allocated_reserved"], r["rental"], r["loan"]) == (2, 1, 0)
    assert r["tenant_id"] == "acme" and str(r["extract_date"]) == "2026-04-01"
    assert r["manifest_sha256"] == "sha123"


def test_quantities_round_not_truncate(spark) -> None:
    # Fractional string qtys must round (bridge ``_i``), not truncate. on_hand sums the
    # rounded components: bround(8.6)=9 + bround(1.4)=1 + bround(2.5)=2 = 12.
    rows = [
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "onhandnew": "8.6", "onhandbad": "1.4",
         "inrepair": "2.5", "allocated": "0", "rentalqty": "0", "loanqty": "0"},
    ]
    out = transform_to_stock_position(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    r = out.collect()[0]
    assert r["serviceable"] == 9  # bround(8.6) -> 9, not trunc -> 8
    assert r["unserviceable_in_repair"] == 2  # bround(2.5) -> 2 (HALF_EVEN)
    assert r["on_hand"] == 12  # 9 + 1 + 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hostpartid", ""),
        ("hostlocid", None),
        ("onhandnew", "not-a-number"),
        ("onhandbad", "Infinity"),
        ("inrepair", "-1"),
        ("allocated", "-Infinity"),
        ("rentalqty", "1e300"),
        ("loanqty", "-1"),
    ],
)
def test_semantically_invalid_stock_row_fails_closed(
    spark,
    field: str,
    value: str | None,
) -> None:
    valid = {
        "hostpartid": "PN-A",
        "hostlocid": "LOC-1",
        "onhandnew": "8",
        "onhandbad": "2",
        "inrepair": "3",
        "allocated": "2",
        "rentalqty": "1",
        "loanqty": "0",
    }

    with pytest.raises(ValueError, match="invalid required fields"):
        transform_to_stock_position(
            spark.createDataFrame([{**valid, field: value}, valid]),
            tenant_id="acme",
            extract_date=date(2026, 4, 1),
            manifest_sha256="sha123",
        )
