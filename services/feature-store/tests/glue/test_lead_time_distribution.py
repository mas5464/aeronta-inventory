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
        # PN-A: preferred V1 (processing 30) must win over cheaper-but-not-preferred V2.
        {"hostpartid": "PN-A", "hostvendorlocid": "V1", "price": "100",
         "processinglength": "30", "preferred": "Y"},
        {"hostpartid": "PN-A", "hostvendorlocid": "V2", "price": "90",
         "processinglength": "999", "preferred": "N"},
        # PN-B: single vendor, processing 40, no realized history -> fallback.
        {"hostpartid": "PN-B", "hostvendorlocid": "V3", "price": "50",
         "processinglength": "40", "preferred": "N"},
        # PN-C: processing 0 -> promised defaults to 21.
        {"hostpartid": "PN-C", "hostvendorlocid": "V5", "price": "10",
         "processinglength": "0", "preferred": "Y"},
    ]


def _closed_rows():
    # PN-A realized lead days = [5, 8, 10, 12, 20] (+ one invalid received<ordered, dropped).
    base = "2026-01-01"
    return [
        {"hostpartid": "PN-A", "planorderdate": base, "actualrcvdate": "2026-01-06"},   # 5
        {"hostpartid": "PN-A", "planorderdate": base, "actualrcvdate": "2026-01-09"},   # 8
        {"hostpartid": "PN-A", "planorderdate": base, "actualrcvdate": "2026-01-11"},   # 10
        {"hostpartid": "PN-A", "planorderdate": base, "actualrcvdate": "2026-01-13"},   # 12
        {"hostpartid": "PN-A", "planorderdate": base, "actualrcvdate": "2026-01-21"},   # 20
        {"hostpartid": "PN-A", "planorderdate": "2026-02-01", "actualrcvdate": base},   # invalid
    ]


def test_transform_lead_time_with_realized(spark) -> None:
    out = transform_to_lead_time(
        spark.createDataFrame(_price_rows()),
        spark.createDataFrame(_closed_rows()),
        tenant_id="acme", extract_date=date(2026, 4, 1), manifest_sha256="sha123",
    )
    assert out.columns == list(LEAD_TIME_COLUMNS)
    recs = {r["pn"]: r for r in out.collect()}
    assert set(recs) == {"PN-A", "PN-B", "PN-C"}
    for r in recs.values():
        assert (r["vendor"], r["condition"]) == ("DEFAULT", "NEW")

    # PN-A: preferred vendor V1 -> promised 30. realized=[5,8,10,12,20]:
    # n=5, mean=11, p50=realized[2]=10, p90=realized[4]=20, p99=realized[4]=20.
    a = recs["PN-A"]
    assert a["promised_lead_days"] == pytest.approx(30.0)
    assert a["n_observations"] == 5
    assert a["realized_mean_days"] == pytest.approx(11.0)
    assert a["realized_p50_days"] == pytest.approx(10.0)
    assert a["realized_p90_days"] == pytest.approx(20.0)
    assert a["realized_p99_days"] == pytest.approx(20.0)
    assert a["promised_vs_actual_delta_mean"] == pytest.approx(11.0 - 30.0)

    # PN-B: no realized -> mean=p50=promised(40), p90=40*1.3, p99=40*1.6, n=0.
    b = recs["PN-B"]
    assert b["promised_lead_days"] == pytest.approx(40.0)
    assert b["n_observations"] == 0
    assert b["realized_mean_days"] == pytest.approx(40.0)
    assert b["realized_p50_days"] == pytest.approx(40.0)
    assert b["realized_p90_days"] == pytest.approx(52.0)
    assert b["realized_p99_days"] == pytest.approx(64.0)
    assert b["promised_vs_actual_delta_mean"] == pytest.approx(0.0)

    # PN-C: processing 0 -> promised 21; fallback percentiles off 21.
    c = recs["PN-C"]
    assert c["promised_lead_days"] == pytest.approx(21.0)
    assert c["realized_p90_days"] == pytest.approx(27.3)
    assert c["realized_p99_days"] == pytest.approx(33.6)
    assert c["n_observations"] == 0


def test_equal_preferred_tiebreak_is_deterministic(spark) -> None:
    # Two same-preferred (both "N") vendors with different ProcessingLength: the deterministic
    # tie-break is (pref_rank, price, vendor), so the cheaper vendor's processing wins.
    price = [
        {"hostpartid": "PN-D", "hostvendorlocid": "VB", "price": "80",
         "processinglength": "50", "preferred": "N"},
        {"hostpartid": "PN-D", "hostvendorlocid": "VA", "price": "70",
         "processinglength": "60", "preferred": "N"},
    ]
    out = transform_to_lead_time(
        spark.createDataFrame(price), None,
        tenant_id="acme", extract_date=date(2026, 4, 1), manifest_sha256="sha123",
    )
    recs = {r["pn"]: r for r in out.collect()}
    assert recs["PN-D"]["promised_lead_days"] == pytest.approx(60.0)  # cheaper VA (70) wins


def test_transform_lead_time_without_closed_orders(spark) -> None:
    # closed_df None -> every pn falls back to promised-derived values.
    out = transform_to_lead_time(
        spark.createDataFrame(_price_rows()), None,
        tenant_id="acme", extract_date=date(2026, 4, 1), manifest_sha256="sha123",
    )
    recs = {r["pn"]: r for r in out.collect()}
    assert recs["PN-A"]["n_observations"] == 0
    assert recs["PN-A"]["realized_mean_days"] == pytest.approx(30.0)  # = promised
    assert recs["PN-A"]["realized_p90_days"] == pytest.approx(39.0)   # 30 * 1.3
