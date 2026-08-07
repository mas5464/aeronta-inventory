"""requisition_snapshot Glue transform — local Spark contract coverage."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("pyspark")

from trax_io_feature_store.glue.requisition_snapshot_job import (  # noqa: E402
    REQUISITION_COLUMNS,
    select_requisition_artifacts,
    transform_to_requisitions,
)


def test_select_requisition_artifacts_is_manifest_status_authoritative() -> None:
    manifest = {
        "artifacts": [
            {
                "domain": "order_plan_data_requisition",
                "status": "succeeded",
                "s3_uri": "s3://x/requisition.json",
            },
            {
                "domain": "order_plan_data_requisition",
                "status": "failed",
                "s3_uri": "s3://x/stale.json",
            },
            {"domain": "order_plan", "status": "succeeded"},
        ]
    }

    selected = select_requisition_artifacts(manifest)

    assert [artifact["s3_uri"] for artifact in selected] == [
        "s3://x/requisition.json"
    ]


def test_transform_requisitions_and_known_empty_markers(spark) -> None:
    raw = spark.createDataFrame(
        [
            {
                "hostpartid": "PN-A",
                "hostlocid": "LOC-1",
                "hostorderid": "REQ-2",
                "orderstatus": "OPEN",
                "planquantity": "5",
                "receivedquantity": "2",
                "planrcvdate": None,
                "hostreplsourcelocid": "LOC-ALT",
            },
            {
                "hostpartid": "PN-A",
                "hostlocid": "LOC-1",
                "hostorderid": "REQ-1",
                "orderstatus": "OPEN",
                "planquantity": "4",
                "receivedquantity": "0",
                "planrcvdate": "2026-05-01",
                "hostreplsourcelocid": None,
            },
            {
                "hostpartid": "PN-A",
                "hostlocid": "LOC-1",
                "hostorderid": "REQ-CLOSED",
                "orderstatus": "CLOSED",
                "planquantity": "99",
                "receivedquantity": "0",
                "planrcvdate": "2026-05-02",
                "hostreplsourcelocid": None,
            },
        ]
    )
    planning_keys = spark.createDataFrame(
        [
            {"hostpartid": "PN-A", "hostlocid": "LOC-1"},
            {"hostpartid": "PN-ZERO", "hostlocid": "LOC-2"},
        ]
    )

    out = transform_to_requisitions(
        raw,
        tenant_id="acme",
        extract_date=date(2026, 4, 1),
        manifest_sha256="sha",
        planning_keys=planning_keys,
    )

    assert tuple(out.columns) == REQUISITION_COLUMNS
    by_key = {
        (row["pn"], row["location"]): row
        for row in out.collect()
    }
    observed = by_key[("PN-A", "LOC-1")]
    assert observed["total_qty_needed"] == 7
    assert [line["requisition_id"] for line in observed["lines"]] == [
        "REQ-1",
        "REQ-2",
    ]
    assert observed["lines"][0]["qty_needed"] == 4
    assert observed["lines"][0]["need_by"] == date(2026, 5, 1)
    assert observed["lines"][1]["qty_needed"] == 3
    assert observed["lines"][1]["need_by"] is None
    assert observed["lines"][1]["alt_source_location"] == "LOC-ALT"

    empty = by_key[("PN-ZERO", "LOC-2")]
    assert empty["lines"] == []
    assert empty["total_qty_needed"] == 0
    assert str(empty["snapshot_at"]).startswith("2026-04-01")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hostpartid", None),
        ("hostlocid", None),
        ("hostorderid", None),
        ("orderstatus", None),
        ("planquantity", "not-a-number"),
        ("planquantity", "Infinity"),
        ("receivedquantity", "-Infinity"),
        ("planrcvdate", "not-a-date"),
    ],
)
def test_malformed_succeeded_requisition_fails_before_empty_markers(
    spark,
    field,
    value,
) -> None:
    valid = {
        "hostpartid": "PN-A",
        "hostlocid": "LOC-1",
        "hostorderid": "REQ-1",
        "orderstatus": "OPEN",
        "planquantity": "3",
        "receivedquantity": "0",
        "planrcvdate": "2026-05-01",
        "hostreplsourcelocid": "LOC-X",
    }
    malformed = {**valid, field: value}

    with pytest.raises(ValueError, match="invalid required fields"):
        transform_to_requisitions(
            spark.createDataFrame([malformed, valid]),
            tenant_id="acme",
            extract_date=date(2026, 4, 1),
            manifest_sha256="sha",
            planning_keys=spark.createDataFrame(
                [{"hostpartid": "PN-ZERO", "hostlocid": "LOC-2"}]
            ),
        )
