"""current_policy Glue transform — runs against a local SparkSession (skips without Java)."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.current_policy_job import (  # noqa: E402
    CURRENT_POLICY_COLUMNS,
    select_current_policy_artifacts,
    transform_to_current_policy,
)


def test_select_current_policy_artifacts() -> None:
    manifest = {
        "artifacts": [
            {"domain": "stock_level_upload", "status": "succeeded", "s3_uri": "s3://x/y.json"},
            {"domain": "stock_amount", "status": "succeeded"},
        ]
    }
    selected = select_current_policy_artifacts(manifest)
    assert [a["domain"] for a in selected] == ["stock_level_upload"]


def test_transform_current_policy(spark) -> None:
    rows = [
        # #19 alias corrected at source: hostpartid=PN, hostlocid=LOCATION
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "rop": 5, "eoq": 5, "safetylevel": 2,
         "stockmax": 40, "slreplenishmentlength": 60.0},
        {"hostpartid": "PN-A", "hostlocid": "LOC-1", "rop": 5, "eoq": 5, "safetylevel": 2,
         "stockmax": 40, "slreplenishmentlength": 60.0},  # duplicate -> deduped
    ]
    out = transform_to_current_policy(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.columns == list(CURRENT_POLICY_COLUMNS)
    recs = out.collect()
    assert len(recs) == 1
    r = recs[0]
    assert (r["pn"], r["location"]) == ("PN-A", "LOC-1")
    assert (r["rop"], r["eoq"], r["safety_stock"], r["max_stock"]) == (5, 5, 2, 40)
    assert r["replenishment_lead_days"] == 60.0
    assert r["tenant_id"] == "acme" and str(r["extract_date"]) == "2026-04-01"
