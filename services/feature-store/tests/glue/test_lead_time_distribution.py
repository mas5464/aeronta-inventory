"""lead_time_distribution Glue transform — local SparkSession (skips without Java).

Expected values are computed from the bridge ``_lead_time`` formula by hand so this doubles as
a parity check on the index-based percentiles + promised/realized fallback.
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.lead_time_distribution_job import (  # noqa: E402
    LEAD_TIME_COLUMNS,
    select_lead_time_artifacts,
    transform_to_lead_time,
)


def test_select_lead_time_artifacts() -> None:
    manifest = {
        "artifacts": [
            {"domain": "pn_vendor_price", "status": "succeeded", "s3_uri": "s3://x/p.json"},
            {"domain": "order_plan_closed_orders", "status": "succeeded"},  # enrichment
        ]
    }
    sel = select_lead_time_artifacts(manifest)
    assert [a["domain"] for a in sel] == ["pn_vendor_price"]


def _price_rows():
    return [
        {
            "hostpartid": "PN-A",
            "hostvendorlocid": "BUY-1",
            "ordertypeid": "PO",
            "condition": "NEW",
            "price": "100",
            "processinglength": "30",
            "preferred": "Y",
        },
        {
            "hostpartid": "PN-A",
            "hostvendorlocid": "SHOP-1",
            "ordertypeid": "RO",
            "condition": "REP",
            "price": "80",
            "processinglength": "14",
            "preferred": "Y",
        },
        # Legacy price rows without OrderTypeID are classified by condition.
        {
            "hostpartid": "PN-B",
            "hostvendorlocid": "BUY-3",
            "ordertypeid": "",
            "condition": "NEW",
            "price": "50",
            "processinglength": "40",
            "preferred": "N",
        },
        {
            "hostpartid": "PN-B",
            "hostvendorlocid": "SHOP-3",
            "ordertypeid": "",
            "condition": "REP",
            "price": "60",
            "processinglength": "20",
            "preferred": "N",
        },
        # A non-positive promise without observations is not evidence and emits no row.
        {
            "hostpartid": "PN-C",
            "hostvendorlocid": "V5",
            "ordertypeid": "PO",
            "condition": "NEW",
            "price": "10",
            "processinglength": "0",
            "preferred": "Y",
        },
    ]


def _closed_rows():
    base = "2026-01-01"
    return [
        {
            "hostpartid": "PN-A",
            "hostvendorlocid": "BUY-1",
            "ordertypeid": "PO",
            "hostorderid": "PO_1",
            "orderid": "PO_1",
            "planorderdate": base,
            "actualrcvdate": "2026-01-06",
        },
        {
            "hostpartid": "PN-A",
            "hostvendorlocid": "BUY-1",
            "ordertypeid": "PO",
            "hostorderid": "PO_2",
            "orderid": "PO_2",
            "planorderdate": base,
            "actualrcvdate": "2026-01-09",
        },
        {
            "hostpartid": "PN-A",
            "hostvendorlocid": "BUY-1",
            "ordertypeid": "PO",
            "hostorderid": "PO_3",
            "orderid": "PO_3",
            "planorderdate": base,
            "actualrcvdate": "2026-01-11",
        },
        {
            "hostpartid": "PN-A",
            "hostvendorlocid": "SHOP-1",
            "ordertypeid": "RO",
            "hostorderid": "RO_1",
            "orderid": "RO_1",
            "planorderdate": base,
            "actualrcvdate": "2026-01-13",
        },
        {
            "hostpartid": "PN-A",
            "hostvendorlocid": "SHOP-1",
            "ordertypeid": "RO",
            "hostorderid": "RO_2",
            "orderid": "RO_2",
            "planorderdate": base,
            "actualrcvdate": "2026-01-21",
        },
        # Invalid negative cycle and unclassified order are both excluded.
        {
            "hostpartid": "PN-A",
            "hostvendorlocid": "BUY-1",
            "ordertypeid": "PO",
            "hostorderid": "PO_BAD",
            "orderid": "PO_BAD",
            "planorderdate": "2026-02-01",
            "actualrcvdate": base,
        },
        {
            "hostpartid": "PN-A",
            "hostvendorlocid": "BUY-1",
            "ordertypeid": "",
            "hostorderid": "UNKNOWN",
            "orderid": "UNKNOWN",
            "planorderdate": base,
            "actualrcvdate": "2026-01-30",
        },
    ]


def test_transform_lead_time_with_realized(spark) -> None:
    out = transform_to_lead_time(
        spark.createDataFrame(_price_rows()),
        spark.createDataFrame(_closed_rows()),
        tenant_id="acme", extract_date=date(2026, 4, 1), manifest_sha256="sha123",
    )
    assert out.columns == list(LEAD_TIME_COLUMNS)
    recs = {
        (r["pn"], r["vendor"], r["condition"]): r
        for r in out.collect()
    }
    assert set(recs) == {
        ("PN-A", "BUY-1", "NEW"),
        ("PN-A", "DEFAULT", "NEW"),
        ("PN-A", "SHOP-1", "REP"),
        ("PN-A", "DEFAULT", "REP"),
        ("PN-B", "BUY-3", "NEW"),
        ("PN-B", "DEFAULT", "NEW"),
        ("PN-B", "SHOP-3", "REP"),
        ("PN-B", "DEFAULT", "REP"),
    }

    procurement = recs["PN-A", "BUY-1", "NEW"]
    assert procurement["promised_lead_days"] == pytest.approx(30.0)
    assert procurement["n_observations"] == 3
    assert procurement["realized_mean_days"] == pytest.approx(23.0 / 3.0)
    assert procurement["realized_p50_days"] == pytest.approx(8.0)
    assert procurement["realized_p90_days"] == pytest.approx(10.0)
    assert procurement["realized_p99_days"] == pytest.approx(10.0)
    assert procurement["promised_vs_actual_delta_mean"] == pytest.approx(
        23.0 / 3.0 - 30.0
    )
    assert procurement["observed_cycle_days"] == [5, 8, 10]
    assert procurement["evidence_status"] == "observed"
    assert procurement["source"] == "order_plan_closed_orders"
    assert procurement["grouping_level"] == "part_vendor_condition"
    assert procurement["confidence"] == "low"
    assert procurement["data_cutoff"] == date(2026, 1, 11)
    assert procurement["model_version"] == "supply-cycle-v2"
    assert procurement["proxy_definition"] is None
    assert procurement["classification_source"] == "explicit_order_type"

    repair = recs["PN-A", "SHOP-1", "REP"]
    assert repair["n_observations"] == 2
    assert repair["realized_mean_days"] == pytest.approx(16.0)
    assert repair["realized_p50_days"] == pytest.approx(20.0)
    assert repair["realized_p90_days"] == pytest.approx(20.0)
    assert repair["observed_cycle_days"] == [12, 20]
    assert repair["data_cutoff"] == date(2026, 1, 21)
    assert repair["proxy_definition"] == "order_creation_to_last_receipt"
    assert repair["classification_source"] == "explicit_order_type"

    assert recs["PN-A", "DEFAULT", "NEW"]["grouping_level"] == "part_condition"
    assert recs["PN-A", "DEFAULT", "REP"]["grouping_level"] == "part_condition"

    # Configured fallback is a degenerate promise, never invented variance.
    for key, promised in (
        (("PN-B", "BUY-3", "NEW"), 40.0),
        (("PN-B", "DEFAULT", "NEW"), 40.0),
        (("PN-B", "SHOP-3", "REP"), 20.0),
        (("PN-B", "DEFAULT", "REP"), 20.0),
    ):
        fallback = recs[key]
        assert fallback["evidence_status"] == "configured_fallback"
        assert fallback["source"] == "pn_vendor_price"
        assert fallback["n_observations"] == 0
        assert fallback["observed_cycle_days"] == []
        assert fallback["realized_mean_days"] == pytest.approx(promised)
        assert fallback["realized_p50_days"] == pytest.approx(promised)
        assert fallback["realized_p90_days"] == pytest.approx(promised)
        assert fallback["realized_p99_days"] == pytest.approx(promised)
        assert fallback["promised_vs_actual_delta_mean"] is None
        assert fallback["classification_source"] == "configured_condition"

    assert (
        recs["PN-B", "SHOP-3", "REP"]["proxy_definition"]
        == "configured_repair_promise"
    )


def test_equal_preferred_tiebreak_is_deterministic(spark) -> None:
    # Two same-preferred (both "N") vendors with different ProcessingLength: the deterministic
    # tie-break is (pref_rank, price, vendor), so the cheaper vendor's processing wins.
    price = [
        {"hostpartid": "PN-D", "hostvendorlocid": "VB", "price": "80",
         "processinglength": "50", "preferred": "N", "ordertypeid": "PO"},
        {"hostpartid": "PN-D", "hostvendorlocid": "VA", "price": "70",
         "processinglength": "60", "preferred": "N", "ordertypeid": "PO"},
    ]
    out = transform_to_lead_time(
        spark.createDataFrame(price), None,
        tenant_id="acme", extract_date=date(2026, 4, 1), manifest_sha256="sha123",
    )
    recs = {(r["vendor"], r["condition"]): r for r in out.collect()}
    assert recs["DEFAULT", "NEW"]["promised_lead_days"] == pytest.approx(60.0)
    assert recs["VA", "NEW"]["promised_lead_days"] == pytest.approx(60.0)
    assert recs["VB", "NEW"]["promised_lead_days"] == pytest.approx(50.0)


def test_transform_lead_time_without_closed_orders(spark) -> None:
    out = transform_to_lead_time(
        spark.createDataFrame(_price_rows()), None,
        tenant_id="acme", extract_date=date(2026, 4, 1), manifest_sha256="sha123",
    )
    recs = {
        (r["pn"], r["vendor"], r["condition"]): r
        for r in out.collect()
    }
    assert ("PN-C", "V5", "NEW") not in recs
    for row in recs.values():
        assert row["n_observations"] == 0
        assert row["realized_mean_days"] == row["promised_lead_days"]
        assert row["realized_p50_days"] == row["promised_lead_days"]
        assert row["realized_p90_days"] == row["promised_lead_days"]
        assert row["realized_p99_days"] == row["promised_lead_days"]
        assert row["promised_vs_actual_delta_mean"] is None


def test_legacy_order_prefix_produces_observed_repair_lane(spark) -> None:
    price = spark.createDataFrame(
        [
            {
                "hostpartid": "PN-L",
                "hostvendorlocid": "SHOP-L",
                "ordertypeid": "",
                "condition": "REP",
                "processinglength": "0",
                "preferred": "Y",
                "price": "1",
            }
        ]
    )
    closed = spark.createDataFrame(
        [
            {
                "hostpartid": "PN-L",
                "hostvendorlocid": "SHOP-L",
                "ordertypeid": "",
                "hostorderid": "RO-123",
                "orderid": "RO-123",
                "planorderdate": "2026-01-01",
                "actualrcvdate": "2026-01-16",
            }
        ]
    )

    rows = transform_to_lead_time(
        price,
        closed,
        tenant_id="acme",
        extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    ).collect()

    assert {
        (row["vendor"], row["condition"])
        for row in rows
    } == {("SHOP-L", "REP"), ("DEFAULT", "REP")}
    for row in rows:
        assert row["promised_lead_days"] is None
        assert row["n_observations"] == 1
        assert row["realized_mean_days"] == 15.0
        assert row["classification_source"] == "legacy_order_id_prefix"


def test_closed_only_observations_are_not_lost_without_price_artifact(spark) -> None:
    closed = spark.createDataFrame(
        [
            {
                "hostpartid": "PN-CLOSED",
                "hostvendorlocid": "BUY-CLOSED",
                "ordertypeid": "PO",
                "planorderdate": "2026-02-01",
                "actualrcvdate": "2026-02-12",
            }
        ]
    )

    rows = transform_to_lead_time(
        None,
        closed,
        tenant_id="acme",
        extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    ).collect()

    assert {
        (row["vendor"], row["condition"])
        for row in rows
    } == {("BUY-CLOSED", "NEW"), ("DEFAULT", "NEW")}
    for row in rows:
        assert row["promised_lead_days"] is None
        assert row["promised_vs_actual_delta_mean"] is None
        assert row["evidence_status"] == "observed"
        assert row["data_cutoff"] == date(2026, 2, 12)


@pytest.mark.parametrize(
    ("ordertypeid", "condition", "expected_source"),
    [
        ("PO", "NEW", "explicit_order_type"),
        ("", "REP", "configured_condition"),
        ("", "", "legacy_default_new"),
    ],
)
def test_configured_lane_classification_provenance(
    spark,
    ordertypeid: str,
    condition: str,
    expected_source: str,
) -> None:
    price = spark.createDataFrame(
        [
            {
                "hostpartid": "PN-CONFIG",
                "hostvendorlocid": "V-CONFIG",
                "ordertypeid": ordertypeid,
                "condition": condition,
                "processinglength": "9",
                "preferred": "Y",
                "price": "1",
            }
        ]
    )

    rows = transform_to_lead_time(
        price,
        None,
        tenant_id="acme",
        extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    ).collect()

    assert rows
    assert {row["classification_source"] for row in rows} == {expected_source}
    assert {row["data_cutoff"] for row in rows} == {date(2026, 4, 1)}


@pytest.mark.parametrize(
    ("n_observations", "expected_confidence"),
    [(9, "low"), (10, "medium"), (29, "medium"), (30, "high")],
)
def test_observed_confidence_boundaries(
    spark,
    n_observations: int,
    expected_confidence: str,
) -> None:
    closed = spark.createDataFrame(
        [
            {
                "hostpartid": "PN-CONFIDENCE",
                "hostvendorlocid": "V-CONFIDENCE",
                "ordertypeid": "PO",
                "planorderdate": "2026-01-01",
                "actualrcvdate": f"2026-01-{index % 20 + 2:02d}",
            }
            for index in range(n_observations)
        ]
    )

    rows = transform_to_lead_time(
        None,
        closed,
        tenant_id="acme",
        extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    ).collect()

    assert {row["confidence"] for row in rows} == {expected_confidence}
