"""interchangeable_graph Glue transform — runs against a local SparkSession (skips without Java)."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.interchangeable_graph_job import (  # noqa: E402
    INTERCHANGEABLE_GRAPH_COLUMNS,
    select_interchangeable_graph_artifacts,
    transform_to_interchangeable_graph,
)


def test_select_interchangeable_graph_artifacts() -> None:
    manifest = {
        "artifacts": [
            {"domain": "part_chain_details", "status": "succeeded", "s3_uri": "s3://x/c.json"},
            {"domain": "part_chain_details", "status": "failed"},
            {"domain": "part_chain", "status": "succeeded"},
        ]
    }
    sel = select_interchangeable_graph_artifacts(manifest)
    assert [a["domain"] for a in sel] == ["part_chain_details"]


def test_transform_interchangeable_graph(spark) -> None:
    rows = [
        {"hostpartid": "PN-1", "hostchainparentid": "PN-2", "relationtype": "0"},  # two-way
        {"hostpartid": "PN-2", "hostchainparentid": "PN-3", "relationtype": "1"},  # one-way
        {"hostpartid": "PN-1", "hostchainparentid": "PN-1", "relationtype": "0"},  # self -> skip
        {"hostpartid": "PN-1", "hostchainparentid": None, "relationtype": "0"},     # null -> skip
        {"hostpartid": None, "hostchainparentid": "PN-2", "relationtype": "0"},     # null -> skip
    ]
    out = transform_to_interchangeable_graph(
        spark.createDataFrame(rows), tenant_id="acme", extract_date=date(2026, 4, 1),
        manifest_sha256="sha123",
    )
    assert out.columns == list(INTERCHANGEABLE_GRAPH_COLUMNS)
    recs = {r["pn"]: r for r in out.collect()}
    assert set(recs) == {"PN-1", "PN-2", "PN-3"}

    # members = every PN each node shares an edge with (incl. itself), sorted & distinct.
    assert recs["PN-1"]["members"] == ["PN-1", "PN-2"]
    assert recs["PN-2"]["members"] == ["PN-1", "PN-2", "PN-3"]
    assert recs["PN-3"]["members"] == ["PN-2", "PN-3"]

    # group_id = "+".join(sorted(members))
    assert recs["PN-1"]["group_id"] == "PN-1+PN-2"
    assert recs["PN-2"]["group_id"] == "PN-1+PN-2+PN-3"
    assert recs["PN-3"]["group_id"] == "PN-2+PN-3"

    # PN-2 carries both its edges, sorted by (from_pn, to_pn); one_way flags preserved.
    e2 = recs["PN-2"]["edges"]
    assert [(e["from_pn"], e["to_pn"], e["one_way"]) for e in e2] == [
        ("PN-1", "PN-2", False),
        ("PN-2", "PN-3", True),
    ]
    # The two-way edge (1<->2) is shared by PN-1; the one-way edge by PN-3.
    assert [(e["from_pn"], e["to_pn"], e["one_way"]) for e in recs["PN-1"]["edges"]] == [
        ("PN-1", "PN-2", False)
    ]
    assert recs["PN-3"]["edges"][0]["one_way"] is True
    assert recs["PN-1"]["tenant_id"] == "acme"
