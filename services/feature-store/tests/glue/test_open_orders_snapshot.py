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


def test_transform_preserves_nonterminal_repair_evidence_without_crediting_non_open_po(
    spark,
) -> None:
    rows = [
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "IN_PROGRESS",
            "ordertypeid": "RO",
            "hostorderid": "RO-101",
            "orderlineid": "1",
            "planquantity": "1",
            "receivedquantity": "0",
            "planrcvdate": "",
            "planorderdate": "04/02/2026 13:15",
            "hostvendorlocid": "VENDOR-1",
            "hostshopid": "SHOP-1",
            "serialnumber": "SER-1",
        },
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "AWAITING_PARTS",
            "ordertypeid": "RO",
            "hostorderid": "RO-102",
            "orderlineid": None,
            "planquantity": "2",
            "receivedquantity": "0",
            "planrcvdate": "",
            "planorderdate": None,
            "hostvendorlocid": "VENDOR-2",
            "hostshopid": "SHOP-2",
            "serialnumber": None,
        },
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "IN_PROGRESS",
            "ordertypeid": "PO",
            "hostorderid": "PO-NOT-OPEN",
            "orderlineid": "1",
            "planquantity": "9",
            "receivedquantity": "0",
            "planrcvdate": "2026-05-01",
            "planorderdate": "2026-04-01",
            "hostvendorlocid": "VENDOR-3",
            "hostshopid": None,
            "serialnumber": None,
        },
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "OPEN",
            "ordertypeid": "PO",
            "hostorderid": "PO-OPEN",
            "orderlineid": "3",
            "planquantity": "4",
            "receivedquantity": "0",
            "planrcvdate": "2026-05-02",
            "planorderdate": "2026-04-03",
            "hostvendorlocid": "VENDOR-4",
            "hostshopid": None,
            "serialnumber": None,
        },
    ]

    result = transform_to_open_orders(
        spark.createDataFrame(rows),
        tenant_id="acme",
        extract_date=date(2026, 4, 15),
        manifest_sha256="sha123",
    ).collect()

    assert len(result) == 1
    assert result[0]["total_open_qty"] == 7
    by_id = {order["order_id"]: order for order in result[0]["orders"]}
    assert set(by_id) == {"RO-101", "RO-102", "PO-OPEN"}
    assert by_id["RO-101"].asDict() == {
        "order_id": "RO-101",
        "order_type": "RO",
        "vendor": "VENDOR-1",
        "qty_open": 1,
        "expected_rcv_date": None,
        "order_line_id": "1",
        "opened_at": by_id["RO-101"]["opened_at"],
        "status": "IN_PROGRESS",
        "serial_number": "SER-1",
        "shop": "SHOP-1",
        "location": "LOC-1",
    }
    assert str(by_id["RO-101"]["opened_at"]).startswith("2026-04-02 13:15")
    assert by_id["RO-102"]["order_line_id"] is None
    assert by_id["RO-102"]["opened_at"] is None
    assert by_id["RO-102"]["status"] == "AWAITING_PARTS"
    assert by_id["PO-OPEN"]["status"] == "OPEN"


def test_transform_never_defaults_unclassified_order_type_to_purchase(
    spark,
    caplog,
) -> None:
    rows = [
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "OPEN",
            "ordertypeid": None,
            "hostorderid": "PO-LEGACY",
            "orderid": None,
            "planquantity": "3",
            "receivedquantity": "0",
            "planrcvdate": "2026-05-02",
        },
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "IN_PROGRESS",
            "ordertypeid": "",
            "hostorderid": "RO/LEGACY",
            "orderid": None,
            "planquantity": "2",
            "receivedquantity": "0",
            "planrcvdate": "",
        },
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "OPEN",
            "ordertypeid": None,
            "hostorderid": "NO-SAFE-PREFIX",
            "orderid": None,
            "planquantity": "11",
            "receivedquantity": "0",
            "planrcvdate": "2026-05-02",
        },
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "OPEN",
            "ordertypeid": "UNKNOWN",
            "hostorderid": "PO-EXPLICIT-CONFLICT",
            "orderid": None,
            "planquantity": "13",
            "receivedquantity": "0",
            "planrcvdate": "2026-05-02",
        },
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "OPEN",
            "ordertypeid": None,
            "hostorderid": "PO-CONFLICT",
            "orderid": "RO-CONFLICT",
            "planquantity": "17",
            "receivedquantity": "0",
            "planrcvdate": "2026-05-02",
        },
    ]

    result = transform_to_open_orders(
        spark.createDataFrame(rows),
        tenant_id="acme",
        extract_date=date(2026, 4, 15),
        manifest_sha256="sha123",
    ).collect()

    assert len(result) == 1
    assert {
        (order["order_id"], order["order_type"], order["qty_open"])
        for order in result[0]["orders"]
    } == {
        ("PO-LEGACY", "PO", 3),
        ("RO/LEGACY", "RO", 2),
    }
    assert result[0]["total_open_qty"] == 5
    assert "excluded 3 open-order row(s) with unclassified order type" in caplog.text


def test_transform_preserves_terminal_repair_status_as_exclusion_evidence(
    spark,
) -> None:
    rows = [
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "CLOSED",
            "ordertypeid": "RO",
            "hostorderid": "RO-CLOSED",
            "orderlineid": "1",
            "planquantity": "1",
            "receivedquantity": "0",
            "planrcvdate": "",
            "planorderdate": "2026-04-01",
        },
    ]

    result = transform_to_open_orders(
        spark.createDataFrame(rows),
        tenant_id="acme",
        extract_date=date(2026, 4, 15),
        manifest_sha256="sha123",
    ).collect()

    assert len(result) == 1
    assert result[0]["total_open_qty"] == 1
    assert result[0]["orders"][0]["order_id"] == "RO-CLOSED"
    assert result[0]["orders"][0]["order_type"] == "RO"
    assert result[0]["orders"][0]["status"] == "CLOSED"


def test_blank_key_rows_fail_closed(spark) -> None:
    # A non-empty succeeded artifact containing blank identities is corrupt;
    # it must fail rather than being reinterpreted as observed-empty.
    rows = [
        {"hostpartid": "", "hostlocid": "LOC-1", "orderstatus": "OPEN", "ordertypeid": "PO",
         "hostorderid": "O1", "planquantity": "5", "receivedquantity": "0", "planrcvdate": ""},
        {"hostpartid": "PN-A", "hostlocid": "   ", "orderstatus": "OPEN", "ordertypeid": "PO",
         "hostorderid": "O2", "planquantity": "5", "receivedquantity": "0", "planrcvdate": ""},
    ]
    with pytest.raises(ValueError, match="invalid required fields"):
        transform_to_open_orders(
            spark.createDataFrame(rows),
            tenant_id="acme",
            extract_date=date(2026, 4, 1),
            manifest_sha256="sha123",
        )


def test_successful_feed_emits_known_empty_snapshot_for_each_planning_key(
    spark,
) -> None:
    rows = [
        {
            "hostpartid": "PN-A",
            "hostlocid": "LOC-1",
            "orderstatus": "OPEN",
            "ordertypeid": "PO",
            "hostorderid": "O1",
            "planquantity": "3",
            "receivedquantity": "0",
            "planrcvdate": "2026-04-10",
        }
    ]
    planning_keys = spark.createDataFrame(
        [
            {"hostpartid": "PN-A", "hostlocid": "LOC-1"},
            {"hostpartid": "PN-ZERO", "hostlocid": "LOC-2"},
        ]
    )

    out = transform_to_open_orders(
        spark.createDataFrame(rows),
        tenant_id="acme",
        extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
        planning_keys=planning_keys,
    ).collect()
    by_key = {(row["pn"], row["location"]): row for row in out}

    assert by_key[("PN-A", "LOC-1")]["total_open_qty"] == 3
    assert len(by_key[("PN-A", "LOC-1")]["orders"]) == 1
    assert by_key[("PN-ZERO", "LOC-2")]["total_open_qty"] == 0
    assert by_key[("PN-ZERO", "LOC-2")]["orders"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hostpartid", None),
        ("hostlocid", None),
        ("orderstatus", None),
        ("planquantity", "not-a-number"),
        ("planquantity", "Infinity"),
        ("receivedquantity", "-Infinity"),
        ("planrcvdate", "not-a-date"),
    ],
)
def test_malformed_succeeded_order_feed_fails_before_empty_markers(
    spark,
    field,
    value,
) -> None:
    valid = {
        "hostpartid": "PN-A",
        "hostlocid": "LOC-1",
        "orderstatus": "OPEN",
        "ordertypeid": "PO",
        "hostorderid": "O1",
        "planquantity": "3",
        "receivedquantity": "0",
        "planrcvdate": "2026-04-10",
    }
    malformed = {**valid, field: value}

    with pytest.raises(ValueError, match="invalid required fields"):
        transform_to_open_orders(
            spark.createDataFrame([malformed, valid]),
            tenant_id="acme",
            extract_date=date(2026, 4, 1),
            manifest_sha256="sha",
            planning_keys=spark.createDataFrame(
                [{"hostpartid": "PN-ZERO", "hostlocid": "LOC-2"}]
            ),
        )
