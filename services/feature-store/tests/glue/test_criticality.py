"""criticality Glue transform — runs against a local SparkSession (skips without Java)."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.criticality_job import (  # noqa: E402
    CRITICALITY_COLUMNS,
    select_criticality_artifacts,
    transform_to_criticality,
)


def test_select_criticality_artifacts() -> None:
    manifest = {
        "artifacts": [
            {"domain": "part_master", "status": "succeeded", "s3_uri": "s3://x/pm.json"},
            {"domain": "part_criticality", "status": "succeeded"},  # not this job's source
        ]
    }
    sel = select_criticality_artifacts(manifest)
    assert [a["domain"] for a in sel] == ["part_master"]


def test_transform_criticality_maps_canonical_tier(spark) -> None:
    rows = [
        {"hostpartid": "PN-A", "hostpartcriticalid": "AOG"},      # -> tier 1
        {"hostpartid": "PN-B", "hostpartcriticalid": "GO-IF"},    # -> tier 2
        {"hostpartid": "PN-C", "hostpartcriticalid": "weird"},    # unknown -> default 4
        {"hostpartid": "PN-D", "hostpartcriticalid": ""},         # blank -> raw "0", tier 4
        {"hostpartid": "PN-E", "hostpartcriticalid": "5"},        # -> tier 5
        {"hostpartid": "PN-F", "hostpartcriticalid": "consumable"},  # case-insensitive -> 5
        {"hostpartid": "PN-A", "hostpartcriticalid": "AOG"},      # dup -> deduped
        {"hostpartid": None, "hostpartcriticalid": "AOG"},        # null pn -> dropped
    ]
    out = transform_to_criticality(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.columns == list(CRITICALITY_COLUMNS)
    recs = {r["pn"]: r for r in out.collect()}
    assert set(recs) == {"PN-A", "PN-B", "PN-C", "PN-D", "PN-E", "PN-F"}

    assert recs["PN-A"]["canonical_tier"] == 1
    assert recs["PN-B"]["canonical_tier"] == 2
    assert recs["PN-C"]["canonical_tier"] == 4  # unknown code -> default
    assert recs["PN-C"]["raw_essentiality_code"] == "weird"  # original case preserved
    assert recs["PN-D"]["canonical_tier"] == 4
    assert recs["PN-D"]["raw_essentiality_code"] == "0"  # blank normalized to "0"
    assert recs["PN-E"]["canonical_tier"] == 5
    assert recs["PN-F"]["canonical_tier"] == 5  # lower-case "consumable" matched

    for r in recs.values():
        assert r["mapping_source"] == "auto_inferred"
        assert 1 <= r["canonical_tier"] <= 5
        assert r["tenant_id"] == "acme"
