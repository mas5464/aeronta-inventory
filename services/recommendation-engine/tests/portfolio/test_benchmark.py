from decimal import Decimal

import pytest
from pydantic import ValidationError

from trax_io_reco.portfolio.benchmark import (
    FullNetworkBenchmarkConfig,
    build_full_network_benchmark_request,
    run_full_network_benchmark,
)


def _small_config() -> FullNetworkBenchmarkConfig:
    return FullNetworkBenchmarkConfig(
        tenant_ids=("tenant-a", "tenant-b"),
        key_count_per_tenant=20,
        repair_evidence_coverage=Decimal("0.25"),
        solver_time_limit_seconds=5,
        batch_window_seconds=30,
    )


def test_benchmark_fixture_matches_candidate_repair_and_floor_profile() -> None:
    config = _small_config()
    request, repair_count = build_full_network_benchmark_request(
        config,
        "tenant-a",
    )

    assert len(request.menus) == 20
    assert sum(len(menu.frontier.candidates) for menu in request.menus) == 60
    assert repair_count == 5
    assert sum(bool(menu.mandatory_floors) for menu in request.menus) == 4
    assert {
        evidence.source
        for menu in request.menus
        for candidate in menu.frontier.candidates
        for evidence in candidate.evidence
        if evidence.kind == "repair_return"
    } == {
        "completed-and-censored-repair-history",
        "typed-unavailable-repair-history",
    }


def test_concurrent_benchmark_proves_isolation_and_explicit_solver_state() -> None:
    result = run_full_network_benchmark(_small_config())

    assert result.launch_gate_passed
    assert result.concurrent_tenant_isolation_passed
    assert result.bounded_gap_evidence_passed
    assert result.batch_window_passed
    assert {item.tenant_id for item in result.tenant_results} == {
        "tenant-a",
        "tenant-b",
    }
    assert len(
        {item.planning_fingerprint for item in result.tenant_results}
    ) == 2
    assert all(item.key_count == item.selected_key_count == 20 for item in result.tenant_results)
    assert all(
        item.optimality_proven
        or (
            item.solver_termination == "not_proven"
            and item.objective_bound is not None
            and item.relative_gap is not None
        )
        for item in result.tenant_results
    )


def test_full_network_gate_requires_at_least_two_unique_tenants() -> None:
    with pytest.raises(
        ValidationError,
        match="requires concurrent tenants",
    ):
        FullNetworkBenchmarkConfig(tenant_ids=("tenant-a",))

    with pytest.raises(
        ValidationError,
        match="must be unique",
    ):
        FullNetworkBenchmarkConfig(tenant_ids=("tenant-a", "tenant-a"))

    with pytest.raises(ValidationError):
        FullNetworkBenchmarkConfig(
            solver_time_limit_seconds=601,
        )
