from __future__ import annotations

import json
from datetime import date

from trax_io_feature_store.glue.extract_run_status_job import (
    EXTRACT_RUN_STATUS_COLUMNS,
    canonical_artifact_statuses,
    transform_manifest_status,
)


def test_duplicate_or_conflicting_manifest_statuses_fail_closed() -> None:
    manifest = {
        "artifacts": [
            {"domain": "order_plan", "status": "succeeded"},
            {"domain": "order_plan", "status": "failed"},
            {
                "domain": "order_plan_data_requisition",
                "status": "succeeded",
            },
            {
                "domain": "order_plan_data_requisition",
                "status": "succeeded",
            },
        ]
    }

    assert canonical_artifact_statuses(manifest) == {
        "order_plan": "conflict",
        "order_plan_data_requisition": "succeeded",
    }


def test_manifest_status_row_is_typed_and_deterministic(spark) -> None:
    manifest = {
        "run_id": "RUN-1",
        "run_status": "partial",
        "source_sql_sha256": "sha",
        "artifacts": [
            {"domain": "stock_amount", "status": "succeeded"},
            {"domain": "order_plan", "status": "failed"},
        ],
    }

    rows = transform_manifest_status(
        spark,
        manifest,
        tenant_id="acme",
        extract_date=date(2026, 4, 1),
    ).collect()

    assert len(rows) == 1
    row = rows[0]
    assert tuple(row.asDict()) == EXTRACT_RUN_STATUS_COLUMNS
    assert row["run_id"] == "RUN-1"
    assert row["run_status"] == "partial"
    assert json.loads(row["artifact_status_json"]) == {
        "order_plan": "failed",
        "stock_amount": "succeeded",
    }
    assert row["tenant_id"] == "acme"
    assert row["extract_date"] == date(2026, 4, 1)
    assert row["ingested_at"] is not None
