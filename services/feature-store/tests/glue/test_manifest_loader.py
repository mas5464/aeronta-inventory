"""Unit tests for the pure manifest-filter helper (no Spark required)."""

from __future__ import annotations

from trax_io_feature_store.glue.demand_history_job import select_demand_artifacts


def _manifest(artifacts: list[dict]) -> dict:
    return {
        "schema_version": "1.0.0",
        "tenant_id": "aircanada",
        "artifacts": artifacts,
    }


def _artifact(domain: str, status: str) -> dict:
    return {
        "domain": domain,
        "status": status,
        "s3_uri": f"s3://bucket/{domain}.json" if status == "succeeded" else None,
        "row_count": 10 if status == "succeeded" else 0,
    }


def test_both_demand_domains_succeeded():
    m = _manifest(
        [
            _artifact("demand_history_rotables", "succeeded"),
            _artifact("demand_history_expendables", "succeeded"),
            _artifact("causal_values", "succeeded"),
        ]
    )
    selected = select_demand_artifacts(m)
    assert {a["domain"] for a in selected} == {
        "demand_history_rotables",
        "demand_history_expendables",
    }


def test_only_rotables_succeeded():
    m = _manifest(
        [
            _artifact("demand_history_rotables", "succeeded"),
            _artifact("demand_history_expendables", "failed"),
        ]
    )
    selected = select_demand_artifacts(m)
    assert [a["domain"] for a in selected] == ["demand_history_rotables"]


def test_neither_demand_domain_present():
    m = _manifest([_artifact("causal_values", "succeeded")])
    assert select_demand_artifacts(m) == []


def test_failed_status_excluded():
    m = _manifest(
        [
            _artifact("demand_history_rotables", "failed"),
            _artifact("demand_history_expendables", "failed"),
        ]
    )
    assert select_demand_artifacts(m) == []


def test_skipped_status_excluded():
    m = _manifest([_artifact("demand_history_rotables", "skipped")])
    assert select_demand_artifacts(m) == []


def test_empty_artifacts_list():
    assert select_demand_artifacts({"artifacts": []}) == []


def test_missing_artifacts_key():
    assert select_demand_artifacts({}) == []
