"""vendor_economics Glue transform — runs against a local SparkSession (skips without Java).

Numeric fields are passed as **strings** to mirror the real extract (every eMRO value lands
as text), and the SparkSession fixture pins ANSI off like Glue 4.0 — so these tests exercise
the same string->numeric cast path that runs in production.
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.vendor_economics_job import (  # noqa: E402
    VENDOR_ECONOMICS_COLUMNS,
    select_vendor_economics_artifacts,
    transform_to_vendor_economics,
)


def test_select_vendor_economics_artifacts() -> None:
    manifest = {
        "artifacts": [
            {"domain": "pn_vendor_price", "status": "succeeded", "s3_uri": "s3://x/p.json"},
            {"domain": "pn_vendor_price", "status": "failed"},
            {"domain": "part_master", "status": "succeeded"},  # enrichment, not selected here
        ]
    }
    sel = select_vendor_economics_artifacts(manifest)
    assert [a["domain"] for a in sel] == ["pn_vendor_price"]


def _by_key(recs):
    return {(r["pn"], r["vendor"]): r for r in recs}


def test_transform_keeps_per_vendor_and_synthesizes_default(spark) -> None:
    price = [
        {"hostpartid": "PN-A", "hostvendorlocid": "V1", "price": "100", "minoq": "2",
         "preferred": "N"},
        {"hostpartid": "PN-A", "hostvendorlocid": "V2", "price": "120", "minoq": "5",
         "preferred": "Y"},
        {"hostpartid": "PN-B", "hostvendorlocid": "V9", "price": "50", "minoq": "0",
         "preferred": "N"},
        {"hostpartid": None, "hostvendorlocid": "V1", "price": "9", "minoq": "1",
         "preferred": "N"},
    ]
    part_master = [
        {"hostpartid": "PN-A", "marketunitcost": "200", "averagecost": "110",
         "repaircost": "70"},
    ]
    out = transform_to_vendor_economics(
        spark.createDataFrame(price),
        spark.createDataFrame(part_master),
        tenant_id="acme",
        extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.columns == list(VENDOR_ECONOMICS_COLUMNS)
    recs = _by_key(out.collect())
    # 3 real vendor rows + 1 DEFAULT per pn = 5; null-pn row dropped.
    assert len(recs) == 5
    assert {("PN-A", "V1"), ("PN-A", "V2"), ("PN-B", "V9"),
            ("PN-A", "DEFAULT"), ("PN-B", "DEFAULT")} == set(recs)

    # DEFAULT canonical picks the *preferred* vendor (V2), not the cheapest (V1).
    default_a = recs[("PN-A", "DEFAULT")]
    assert float(default_a["unit_cost"]) == 120.0
    assert default_a["minimum_order_qty"] == 5
    assert float(default_a["market_value_unit_cost"]) == 200.0
    assert float(default_a["average_cost"]) == 110.0
    assert float(default_a["repair_cost_24mo_avg"]) == 70.0
    assert default_a["kit_cost"] is None  # getKitCost not ported
    assert default_a["currency"] == "USD"

    # MinOQ floored to 1; PN-B has no part_master row -> costs null.
    b9 = recs[("PN-B", "V9")]
    assert b9["minimum_order_qty"] == 1
    assert b9["market_value_unit_cost"] is None
    assert recs[("PN-B", "DEFAULT")]["minimum_order_qty"] == 1

    # Per-vendor row retains its own price + the part-level costs.
    a1 = recs[("PN-A", "V1")]
    assert float(a1["unit_cost"]) == 100.0 and a1["minimum_order_qty"] == 2
    assert float(a1["repair_cost_24mo_avg"]) == 70.0
    assert a1["tenant_id"] == "acme" and str(a1["extract_date"]) == "2026-04-01"


def test_minoq_rounds_not_truncates(spark) -> None:
    # Fractional MinOQ string must round (bridge ``max(1, _i(minoq, 1))``), not truncate.
    price = [
        {"hostpartid": "PN-A", "hostvendorlocid": "V1", "price": "12.5", "minoq": "4.6",
         "preferred": "Y"},
    ]
    out = transform_to_vendor_economics(
        spark.createDataFrame(price), None, tenant_id="acme",
        extract_date=date(2026, 4, 1), manifest_sha256="sha123",
    )
    recs = _by_key(out.collect())
    assert recs[("PN-A", "V1")]["minimum_order_qty"] == 5  # bround(4.6) -> 5, not trunc -> 4
    assert float(recs[("PN-A", "V1")]["unit_cost"]) == 12.5  # decimal keeps the fraction


def test_transform_without_part_master_yields_null_costs(spark) -> None:
    price = [
        {"hostpartid": "PN-A", "hostvendorlocid": "V1", "price": "100", "minoq": "2",
         "preferred": "Y"},
    ]
    out = transform_to_vendor_economics(
        spark.createDataFrame(price),
        None,
        tenant_id="acme",
        extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    recs = _by_key(out.collect())
    assert set(recs) == {("PN-A", "V1"), ("PN-A", "DEFAULT")}
    for r in recs.values():
        assert r["market_value_unit_cost"] is None
        assert r["average_cost"] is None
        assert r["repair_cost_24mo_avg"] is None
        assert float(r["unit_cost"]) == 100.0
