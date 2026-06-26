"""location_graph Glue transform — runs against a local SparkSession (skips without Java)."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.location_graph_job import (  # noqa: E402
    LOCATION_GRAPH_COLUMNS,
    select_location_graph_artifacts,
    transform_to_location_graph,
)


def test_select_location_graph_artifacts() -> None:
    manifest = {
        "artifacts": [
            {"domain": "location_master", "status": "succeeded", "s3_uri": "s3://x/l.json"},
            {"domain": "location_master", "status": "failed"},
            {"domain": "location_type", "status": "succeeded"},
        ]
    }
    sel = select_location_graph_artifacts(manifest)
    assert [a["domain"] for a in sel] == ["location_master"]


def test_transform_location_graph(spark) -> None:
    rows = [
        {"hostlocid": "YYZ", "hostparentlocid": ""},        # no parent -> main
        {"hostlocid": "YOW", "hostparentlocid": "YYZ"},      # parent -> outstation
        {"hostlocid": "SELF", "hostparentlocid": "SELF"},    # self-parent -> main
        {"hostlocid": "YOW", "hostparentlocid": "YYZ"},      # dup -> deduped
        {"hostlocid": None, "hostparentlocid": "YYZ"},       # null loc -> dropped
    ]
    out = transform_to_location_graph(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.columns == list(LOCATION_GRAPH_COLUMNS)
    recs = {r["location"]: r for r in out.collect()}
    assert set(recs) == {"YYZ", "YOW", "SELF"}

    assert recs["YYZ"]["role"] == "main"
    assert recs["YYZ"]["related_main_warehouse"] is None  # blank parent -> null
    assert recs["YYZ"]["children"] == []  # children deferred (matches bridge)

    assert recs["YOW"]["role"] == "outstation"
    assert recs["YOW"]["related_main_warehouse"] == "YYZ"

    assert recs["SELF"]["role"] == "main"  # parent == location -> main
    assert recs["SELF"]["tenant_id"] == "acme"


def test_conflicting_duplicate_location_is_deterministic(spark) -> None:
    # Two rows for the same location disagree on parent. dropDuplicates would be
    # nondeterministic; dedupe_first picks the lexicographically-smallest parent ("YUL" < "YYZ").
    rows = [
        {"hostlocid": "YOW", "hostparentlocid": "YYZ"},
        {"hostlocid": "YOW", "hostparentlocid": "YUL"},
    ]
    out = transform_to_location_graph(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    recs = out.collect()
    assert len(recs) == 1
    assert recs[0]["related_main_warehouse"] == "YUL"  # deterministic across runs/partitions


def test_blank_location_key_dropped(spark) -> None:
    # Bridge guards reject "" as well as null; a blank key must not emit a junk row.
    rows = [
        {"hostlocid": "", "hostparentlocid": "YYZ"},
        {"hostlocid": "  ", "hostparentlocid": "YYZ"},
        {"hostlocid": "YYC", "hostparentlocid": ""},
    ]
    out = transform_to_location_graph(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    locs = [r["location"] for r in out.collect()]
    assert locs == ["YYC"]  # blank/whitespace keys dropped
