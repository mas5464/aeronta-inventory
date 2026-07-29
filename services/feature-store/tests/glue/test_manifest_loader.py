"""Unit tests for the pure manifest-filter helper (no Spark required)."""

from __future__ import annotations

from datetime import date

import pytest

from trax_io_feature_store.glue._common import validate_manifest_identity
from trax_io_feature_store.glue.demand_history_job import (
    demand_observation_window,
    select_complete_demand_artifacts,
    select_demand_artifacts,
    select_planning_key_artifacts,
)


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


def _demand_artifact(domain: str, *, from_date: str, to_date: str) -> dict:
    artifact = _artifact(domain, "succeeded")
    artifact["bind_vars"] = {"from_date": from_date, "to_date": to_date}
    return artifact


def test_demand_window_comes_from_successful_artifact_binds() -> None:
    manifest = _manifest(
        [
            _demand_artifact(
                "demand_history_rotables",
                from_date="2023-04-16",
                to_date="2026-04-16",
            ),
            _demand_artifact(
                "demand_history_expendables",
                from_date="2023-04-16",
                to_date="2026-04-16",
            ),
        ]
    )

    assert demand_observation_window(manifest) == (
        date(2023, 4, 16),
        date(2026, 4, 16),
    )


def test_disagreeing_demand_windows_fail_loudly() -> None:
    manifest = _manifest(
        [
            _demand_artifact(
                "demand_history_rotables",
                from_date="2023-04-16",
                to_date="2026-04-16",
            ),
            _demand_artifact(
                "demand_history_expendables",
                from_date="2024-04-16",
                to_date="2026-04-16",
            ),
        ]
    )

    with pytest.raises(ValueError, match="disagree"):
        demand_observation_window(manifest)


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


def test_successful_stock_artifact_defines_planning_key_source() -> None:
    stock = _artifact("stock_amount", "succeeded")
    policy = _artifact("stock_level_upload", "succeeded")
    manifest = _manifest(
        [
            stock,
            _artifact("stock_amount", "failed"),
            policy,
        ]
    )

    assert select_planning_key_artifacts(manifest) == [stock, policy]


def test_planning_key_artifacts_fall_back_to_stock_when_policy_is_absent() -> None:
    stock = _artifact("stock_amount", "succeeded")

    assert select_planning_key_artifacts(_manifest([stock])) == [stock]


def test_only_rotables_succeeded():
    m = _manifest(
        [
            _artifact("demand_history_rotables", "succeeded"),
            _artifact("demand_history_expendables", "failed"),
        ]
    )
    selected = select_demand_artifacts(m)
    assert [a["domain"] for a in selected] == ["demand_history_rotables"]
    assert select_complete_demand_artifacts(m) == []


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


def test_manifest_identity_matches_invocation() -> None:
    manifest = {
        "schema_version": "1.0.0",
        "tenant_id": "aircanada",
        "extract_date": "2026-04-16",
        "run_id": "01JRUN",
        "artifacts": [],
    }

    validate_manifest_identity(
        manifest,
        tenant_id="aircanada",
        extract_date=date(2026, 4, 16),
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"schema_version": ""}, "schema_version"),
        ({"schema_version": "2.0.0"}, "schema_version"),
        ({"run_id": "  "}, "run_id"),
        ({"tenant_id": "other"}, "tenant_id"),
        ({"extract_date": "2026-04-15"}, "extract_date"),
        ({"extract_date": "not-a-date"}, "extract_date"),
        ({"artifacts": {}}, "artifacts"),
    ],
)
def test_manifest_identity_rejects_malformed_or_misrouted_runs(
    mutation: dict[str, object],
    message: str,
) -> None:
    manifest = {
        "schema_version": "1.0.0",
        "tenant_id": "aircanada",
        "extract_date": "2026-04-16",
        "run_id": "01JRUN",
        "artifacts": [],
        **mutation,
    }

    with pytest.raises(ValueError, match=message):
        validate_manifest_identity(
            manifest,
            tenant_id="aircanada",
            extract_date=date(2026, 4, 16),
        )
