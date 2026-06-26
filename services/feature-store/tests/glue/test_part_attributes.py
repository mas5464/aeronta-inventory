"""part_attributes Glue transform — runs against a local SparkSession (skips without Java).

Numeric fields are passed as **strings** to mirror the real extract; the fixture pins ANSI
off like Glue 4.0, so the string->int cast path is the one exercised here.
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.part_attributes_job import (  # noqa: E402
    PART_ATTRIBUTES_COLUMNS,
    select_part_attributes_artifacts,
    transform_to_part_attributes,
)


def test_select_part_attributes_artifacts() -> None:
    manifest = {
        "artifacts": [
            {"domain": "part_master", "status": "succeeded", "s3_uri": "s3://x/pm.json"},
            {"domain": "part_master", "status": "failed"},
            {"domain": "stock_amount", "status": "succeeded"},
        ]
    }
    sel = select_part_attributes_artifacts(manifest)
    assert [a["domain"] for a in sel] == ["part_master"]


def test_transform_part_attributes(spark) -> None:
    rows = [
        {"hostpartid": "PN-A", "partdescription": "Widget", "atachapter": "32",
         "ispartkit": "Y", "partserializable": "N", "partrepairable": "N",
         "shelflife": "365", "hazmat": "Y", "tool": "N", "nooftails": "12"},
        {"hostpartid": "PN-A", "partdescription": "Widget", "atachapter": "32",
         "ispartkit": "Y", "partserializable": "N", "partrepairable": "N",
         "shelflife": "365", "hazmat": "Y", "tool": "N", "nooftails": "12"},  # dup -> deduped
        {"hostpartid": "PN-B", "partdescription": "Gizmo", "atachapter": "21",
         "ispartkit": "N", "partserializable": "Y", "partrepairable": "N",
         "shelflife": "0", "hazmat": "N", "tool": "Y", "nooftails": "0"},
        {"hostpartid": "PN-C", "partdescription": "Bolt", "atachapter": "20",
         "ispartkit": "N", "partserializable": "N", "partrepairable": "N",
         "shelflife": None, "hazmat": "N", "tool": "N", "nooftails": None},
        {"hostpartid": None, "partdescription": "x", "atachapter": "00",
         "ispartkit": "N", "partserializable": "N", "partrepairable": "N",
         "shelflife": "1", "hazmat": "N", "tool": "N", "nooftails": "1"},  # null pn -> dropped
    ]
    out = transform_to_part_attributes(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.columns == list(PART_ATTRIBUTES_COLUMNS)
    recs = {r["pn"]: r for r in out.collect()}
    assert set(recs) == {"PN-A", "PN-B", "PN-C"}  # dup deduped, null-pn dropped

    a = recs["PN-A"]
    assert a["part_class"] == "rotable"  # ispartkit wins
    assert a["hazardous_material"] is True and a["tool_control_item"] is False
    assert a["shelf_life_days"] == 365 and a["fleet_effectivity_tail_count"] == 12
    assert a["description"] == "Widget" and a["ata_chapter"] == "32"

    b = recs["PN-B"]
    assert b["part_class"] == "repairable"  # serializable -> repairable
    assert b["shelf_life_days"] is None  # 0 collapsed to null
    assert b["fleet_effectivity_tail_count"] is None  # 0 collapsed to null
    assert b["tool_control_item"] is True and b["hazardous_material"] is False

    assert recs["PN-C"]["part_class"] == "expendable"
    assert recs["PN-C"]["tenant_id"] == "acme"


def test_positive_int_rounds_not_truncates(spark) -> None:
    # Fractional string shelf-life / tail-count must round (bridge ``_i``), not truncate.
    rows = [
        {"hostpartid": "PN-A", "partdescription": "W", "atachapter": "32",
         "ispartkit": "N", "partserializable": "N", "partrepairable": "Y",
         "shelflife": "364.6", "hazmat": "N", "tool": "N", "nooftails": "11.5"},
        {"hostpartid": "PN-Z", "partdescription": "Z", "atachapter": "00",
         "ispartkit": "N", "partserializable": "N", "partrepairable": "N",
         "shelflife": "-3", "hazmat": "N", "tool": "N", "nooftails": "0.4"},
    ]
    out = transform_to_part_attributes(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    recs = {r["pn"]: r for r in out.collect()}
    assert recs["PN-A"]["shelf_life_days"] == 365  # bround(364.6) -> 365, not trunc -> 364
    assert recs["PN-A"]["fleet_effectivity_tail_count"] == 12  # bround(11.5) -> 12 (HALF_EVEN)
    # Negative / sub-0.5 collapse to null (non-positive, and negatives violate NonNegativeInt).
    assert recs["PN-Z"]["shelf_life_days"] is None  # -3 -> null
    assert recs["PN-Z"]["fleet_effectivity_tail_count"] is None  # bround(0.4)=0 -> null
