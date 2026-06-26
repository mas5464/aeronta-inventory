"""open_orders_snapshot Glue transform — runs against a local SparkSession (skips without Java)."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.open_orders_snapshot_job import (  # noqa: E402
    OPEN_ORDERS_COLUMNS,
    select_open_orders_artifacts,
    transform_to_open_orders,
)


def test_select_open_orders_artifacts() -> None:
    manifest = {
        "artifacts": [
            {"domain": "order_plan", "status": "succeeded", "s3_uri": "s3://x/o.json"},
            {"domain": "order_plan", "status": "failed"},
            {"domain": "order_plan_closed_orders", "status": "succeeded"},
        ]
    }
    sel = select_open_orders_artifacts(manifest)
    assert [a["domain"] for a in sel] == ["order_plan"]


def test_transform_open_orders(spark) -> None:
    rows = [
        # OPEN PO with 7 remaining
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "orderstatus": "OPEN", "ordertypeid": "PO",
         "hostorderid": "ORD1", "planquantity": "10", "receivedquantity": "3",
         "planrcvdate": "04/10/2026"},
        # OPEN but fully received -> qty_open 0 -> dropped
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "orderstatus": "OPEN", "ordertypeid": "PO",
         "hostorderid": "ORD2", "planquantity": "5", "receivedquantity": "5",
         "planrcvdate": "04/12/2026"},
        # OPEN RO, blank order id -> "?", 4 remaining
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "orderstatus": "OPEN", "ordertypeid": "RO",
         "hostorderid": "", "planquantity": "4", "receivedquantity": "0",
         "planrcvdate": "2026-04-15"},
        # CLOSED -> filtered out
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "orderstatus": "CLOSED", "ordertypeid": "PO",
         "hostorderid": "ORD3", "planquantity": "9", "receivedquantity": "0",
         "planrcvdate": "04/20/2026"},
        # null pn -> dropped
        {"hostpartid": None, "hostlocid": "LOC-1", "orderstatus": "OPEN", "ordertypeid": "PO",
         "hostorderid": "ORDX", "planquantity": "2", "receivedquantity": "0",
         "planrcvdate": "04/01/2026"},
    ]
    out = transform_to_open_orders(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.columns == list(OPEN_ORDERS_COLUMNS)
    recs = out.collect()
    assert len(recs) == 1  # one (pn, location) group
    r = recs[0]
    assert (r["pn"], r["location"]) == ("PN-A", "LOC-1")
    assert r["total_open_qty"] == 11  # 7 + 4 (ORD2 dropped)
    assert str(r["snapshot_at"]).startswith("2026-04-01")

    orders = r["orders"]
    assert len(orders) == 2  # ORD1 + the blank-id RO; ORD2 (0 qty) and CLOSED excluded
    by_id = {o["order_id"]: o for o in orders}
    assert set(by_id) == {"ORD1", "?"}
    assert by_id["ORD1"]["order_type"] == "PO"
    assert by_id["ORD1"]["qty_open"] == 7
    assert by_id["ORD1"]["vendor"] is None
    assert str(by_id["ORD1"]["expected_rcv_date"]) == "2026-04-10"  # MM/dd/yyyy parsed
    assert by_id["?"]["order_type"] == "RO"
    assert by_id["?"]["qty_open"] == 4
    assert str(by_id["?"]["expected_rcv_date"]) == "2026-04-15"  # ISO parsed

    # orders sorted deterministically by struct (order_id): "?" (0x3F) sorts before "ORD1".
    assert [o["order_id"] for o in orders] == ["?", "ORD1"]


def test_blank_key_rows_dropped(spark) -> None:
    # Blank pn or location must be dropped (bridge truthiness guard), not emitted as a junk key.
    rows = [
        {"hostpartid": "", "hostlocid": "LOC-1", "orderstatus": "OPEN", "ordertypeid": "PO",
         "hostorderid": "O1", "planquantity": "5", "receivedquantity": "0", "planrcvdate": ""},
        {"hostpartid": "PN-A", "hostlocid": "   ", "orderstatus": "OPEN", "ordertypeid": "PO",
         "hostorderid": "O2", "planquantity": "5", "receivedquantity": "0", "planrcvdate": ""},
    ]
    out = transform_to_open_orders(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.collect() == []  # both blank-key rows dropped
