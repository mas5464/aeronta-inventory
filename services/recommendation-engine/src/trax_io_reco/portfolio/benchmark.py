"""Production-representative full-network portfolio benchmark.

The benchmark intentionally executes the real immutable contracts, planning
fingerprint, sparse SciPy/HiGHS model, and result reconciliation.  Synthetic
frontiers are deterministic and contain a configurable sparse repair-evidence
population so the launch gate does not depend on customer data.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from functools import lru_cache
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trax_io_reco.candidate.reconcile import (
    build_no_change_candidate,
    reconcile_candidate,
)
from trax_io_reco.contracts.candidate import (
    CandidateActionLine,
    CandidateEvidence,
    CandidateFrontier,
    CandidateTargetLevels,
    LifecycleEconomics,
    ModelIdentity,
    PolicyCandidate,
    UnitIntervalDecimal,
)
from trax_io_reco.contracts.planning import (
    MAX_PLANNING_SOLVER_SECONDS,
    MandatoryFloor,
    PortfolioKeyMenu,
    PortfolioSolveRequest,
)
from trax_io_reco.portfolio.optimizer import PortfolioOptimizer

FULL_NETWORK_KEY_COUNT = 58_899
DEFAULT_BATCH_WINDOW_SECONDS = 15 * 60
_BASE_FRONTIER = f"frontier_{'0' * 64}"
_BASE_TENANT = "benchmark-prototype"
_BASE_KEY = "PN-000000@MIA"
_MODEL = ModelIdentity(
    forecast_model="compound-poisson",
    forecast_version="benchmark-forecast-v1",
    policy_model="deterministic-s-S",
    policy_version="benchmark-policy-v1",
    repair_model="repair-return",
    repair_version="repair-return.v1",
)
_LEVELS = CandidateTargetLevels(
    rop=2,
    eoq=5,
    safety_stock=2,
    max_stock=20,
)
_ECONOMICS = LifecycleEconomics(
    currency="USD",
    inventory_unit_value=Decimal("100"),
    annual_holding_rate=Decimal("0.25"),
    ordering_cost_per_purchase=Decimal("15"),
    shortage_cost_per_unit=Decimal("250"),
    horizon_days=30,
)


def _digest(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()}"


class _BenchmarkBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class FullNetworkBenchmarkConfig(_BenchmarkBase):
    """Recommended launch-gate defaults for the current 58,899-key network."""

    tenant_ids: tuple[str, ...] = ("benchmark-airline-a", "benchmark-airline-b")
    key_count_per_tenant: int = Field(default=FULL_NETWORK_KEY_COUNT, ge=1)
    repair_evidence_coverage: UnitIntervalDecimal = Decimal("0.20")
    budget_per_key: Decimal = Field(default=Decimal("40"), ge=0)
    solver_time_limit_seconds: float = Field(
        default=300.0,
        gt=0,
        le=MAX_PLANNING_SOLVER_SECONDS,
    )
    batch_window_seconds: float = Field(
        default=DEFAULT_BATCH_WINDOW_SECONDS,
        gt=0,
    )

    @model_validator(mode="after")
    def _concurrent_unique_tenants(self) -> Self:
        if len(self.tenant_ids) < 2:
            raise ValueError("full-network benchmark requires concurrent tenants")
        if len(self.tenant_ids) != len(set(self.tenant_ids)):
            raise ValueError("benchmark tenant ids must be unique")
        if any(not tenant_id.strip() for tenant_id in self.tenant_ids):
            raise ValueError("benchmark tenant ids must be non-empty")
        return self


class TenantBenchmarkResult(_BenchmarkBase):
    tenant_id: str
    planning_fingerprint: str
    status: str
    solver_termination: str
    solver_implementation: str
    solver_implementation_version: str
    optimality_proven: bool
    solver_objective: Decimal | None = None
    objective_bound: Decimal | None = None
    relative_gap: Decimal | None = Field(default=None, ge=0)
    solver_duration_ms: Decimal = Field(ge=0)
    key_count: int
    candidate_count: int
    repair_evidence_key_count: int
    request_build_seconds: float = Field(ge=0)
    solve_seconds: float = Field(ge=0)
    selected_key_count: int = Field(ge=0)
    selected_acquisition_cash: Decimal = Field(ge=0)


class FullNetworkBenchmarkResult(_BenchmarkBase):
    config: FullNetworkBenchmarkConfig
    wall_seconds: float = Field(ge=0)
    tenant_results: tuple[TenantBenchmarkResult, ...]
    concurrent_tenant_isolation_passed: bool
    bounded_gap_evidence_passed: bool
    batch_window_passed: bool
    launch_gate_passed: bool


@lru_cache(maxsize=2)
def _prototype_candidates(
    has_repair_evidence: bool,
) -> tuple[PolicyCandidate, ...]:
    expected_repair_receipts = Decimal("2") if has_repair_evidence else Decimal("0")
    confidence = Decimal("0.80") if has_repair_evidence else Decimal("0.45")
    evidence = (
        CandidateEvidence(
            kind="repair_return",
            source=(
                "completed-and-censored-repair-history"
                if has_repair_evidence
                else "typed-unavailable-repair-history"
            ),
            detail=(
                "Age-conditioned repair-return evidence is available."
                if has_repair_evidence
                else "No observed repair evidence; no inferred repair credit."
            ),
        ),
        CandidateEvidence(
            kind="planning_trace",
            source="synthetic-production-representative-benchmark",
            detail="Deterministic full-network launch-gate fixture.",
        ),
    )
    baseline = build_no_change_candidate(
        frontier_id=_BASE_FRONTIER,
        tenant_id=_BASE_TENANT,
        pn="PN-000000",
        location="MIA",
        decision_key=_BASE_KEY,
        member_keys=(_BASE_KEY,),
        model_identity=_MODEL,
        current_levels=_LEVELS,
        available_before=0,
        expected_receipts_before=expected_repair_receipts,
        projected_demand=10,
        economics=_ECONOMICS,
        expected_aog_risk=Decimal("0.90"),
        confidence=confidence,
        constraints=(),
        evidence=evidence,
    )
    candidates = [baseline]
    for label, quantity, aog_risk in (
        ("Partial replenishment", 5, Decimal("0.50")),
        ("Full replenishment", 10, Decimal("0.10")),
        ("High-service replenishment", 15, Decimal("0.05")),
    ):
        candidates.append(
            reconcile_candidate(
                frontier_id=_BASE_FRONTIER,
                tenant_id=_BASE_TENANT,
                pn="PN-000000",
                location="MIA",
                decision_key=_BASE_KEY,
                member_keys=(_BASE_KEY,),
                candidate_kind="purchase",
                label=label,
                is_no_change=False,
                model_identity=_MODEL,
                current_levels=_LEVELS,
                target_levels=_LEVELS,
                actions=(
                    CandidateActionLine(
                        line_id=f"purchase-{quantity}",
                        kind="purchase",
                        quantity=quantity,
                        currency="USD",
                        unit_acquisition_cash=10,
                        destination_location="MIA",
                    ),
                ),
                available_before=0,
                expected_receipts_before=expected_repair_receipts,
                projected_demand=10,
                economics=_ECONOMICS,
                expected_aog_risk=aog_risk,
                confidence=confidence,
                constraints=(),
                evidence=evidence,
            )
        )
    return tuple(candidates)


def _candidate_count(index: int) -> int:
    # 20% two, 60% three, 20% four candidates: mean 3.0/key.
    bucket = index % 10
    return 2 if bucket < 2 else (3 if bucket < 8 else 4)


def _menu(
    *,
    tenant_id: str,
    index: int,
    has_repair_evidence: bool,
) -> PortfolioKeyMenu:
    pn = f"PN-{index:06d}"
    decision_key = f"{pn}@MIA"
    frontier_id = _digest("frontier", f"{tenant_id}:{decision_key}")
    candidates = []
    for option_index, prototype in enumerate(
        _prototype_candidates(has_repair_evidence)[: _candidate_count(index)]
    ):
        candidates.append(
            prototype.model_copy(
                update={
                    "candidate_id": _digest(
                        "cand",
                        f"{tenant_id}:{decision_key}:{option_index}",
                    ),
                    "tenant_id": tenant_id,
                    "pn": pn,
                    "decision_key": decision_key,
                    "member_keys": (decision_key,),
                }
            )
        )
    canonical_candidates = tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                not candidate.is_no_change,
                candidate.candidate_id,
            ),
        )
    )
    frontier = CandidateFrontier(
        frontier_fingerprint=frontier_id,
        output_digest=_digest("output", f"{tenant_id}:{decision_key}"),
        planner_version="candidate-planner-v1",
        tenant_id=tenant_id,
        decision_key=decision_key,
        member_keys=(decision_key,),
        currency="USD",
        candidates=canonical_candidates,
        total_options_considered=len(canonical_candidates),
        dominated_options_removed=0,
    )
    criticality_tier = (index % 5) + 1
    floors = (
        (
            MandatoryFloor(
                floor_id="critical-service-floor",
                source="benchmark-tenant-policy-v1",
                min_service_level=Decimal("0.50"),
                detail="Criticality tier 1 requires at least 50% expected service.",
            ),
        )
        if criticality_tier == 1
        else ()
    )
    return PortfolioKeyMenu(
        frontier=frontier,
        criticality_tier=criticality_tier,
        mandatory_floors=floors,
    )


def build_full_network_benchmark_request(
    config: FullNetworkBenchmarkConfig,
    tenant_id: str,
) -> tuple[PortfolioSolveRequest, int]:
    """Build one deterministic tenant request and return repair-evidence coverage."""

    if tenant_id not in config.tenant_ids:
        raise ValueError("benchmark tenant is not declared in config")
    repair_evidence_count = int(
        Decimal(config.key_count_per_tenant) * config.repair_evidence_coverage
    )
    menus = tuple(
        _menu(
            tenant_id=tenant_id,
            index=index,
            has_repair_evidence=index < repair_evidence_count,
        )
        for index in range(config.key_count_per_tenant)
    )
    return (
        PortfolioSolveRequest(
            tenant_id=tenant_id,
            source_snapshot_hash=f"benchmark-snapshot-{tenant_id}",
            horizon_days=30,
            currency="USD",
            budget=(
                Decimal(config.key_count_per_tenant)
                * config.budget_per_key
            ),
            menus=menus,
            tenant_policy_version="benchmark-tenant-policy-v1",
            forecast_version="benchmark-forecast-v1",
            repair_model_version="repair-return.v1",
            candidate_planner_version="candidate-planner-v1",
            time_limit_seconds=config.solver_time_limit_seconds,
        ),
        repair_evidence_count,
    )


def _run_tenant(
    config: FullNetworkBenchmarkConfig,
    tenant_id: str,
) -> TenantBenchmarkResult:
    build_started = time.perf_counter()
    request, repair_evidence_count = build_full_network_benchmark_request(
        config,
        tenant_id,
    )
    build_seconds = time.perf_counter() - build_started
    solve_started = time.perf_counter()
    result = PortfolioOptimizer().solve(request)
    solve_seconds = time.perf_counter() - solve_started
    summary = result.summary
    return TenantBenchmarkResult(
        tenant_id=tenant_id,
        planning_fingerprint=result.planning_fingerprint,
        status=result.status,
        solver_termination=result.solver.termination,
        solver_implementation=result.solver.implementation,
        solver_implementation_version=(
            result.solver.implementation_version
        ),
        optimality_proven=result.solver.optimality_proven,
        solver_objective=result.solver.objective,
        objective_bound=result.solver.objective_bound,
        relative_gap=result.solver.relative_gap,
        solver_duration_ms=result.solver.duration_ms,
        key_count=len(request.menus),
        candidate_count=sum(
            len(menu.frontier.candidates) for menu in request.menus
        ),
        repair_evidence_key_count=repair_evidence_count,
        request_build_seconds=build_seconds,
        solve_seconds=solve_seconds,
        selected_key_count=summary.selected_key_count if summary else 0,
        selected_acquisition_cash=(
            summary.selected_acquisition_cash if summary else Decimal("0")
        ),
    )


def run_full_network_benchmark(
    config: FullNetworkBenchmarkConfig | None = None,
) -> FullNetworkBenchmarkResult:
    """Execute all declared tenants concurrently and evaluate the launch gate."""

    effective = config or FullNetworkBenchmarkConfig()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(effective.tenant_ids)) as executor:
        tenant_results = tuple(
            sorted(
                executor.map(
                    lambda tenant_id: _run_tenant(effective, tenant_id),
                    effective.tenant_ids,
                ),
                key=lambda item: item.tenant_id,
            )
        )
    wall_seconds = time.perf_counter() - started
    tenant_isolation = (
        {result.tenant_id for result in tenant_results}
        == set(effective.tenant_ids)
        and len(
            {result.planning_fingerprint for result in tenant_results}
        )
        == len(effective.tenant_ids)
        and all(
            result.key_count == effective.key_count_per_tenant
            and result.selected_key_count == effective.key_count_per_tenant
            for result in tenant_results
        )
    )
    bounded_gap = all(
        result.status == "completed"
        and (
            result.optimality_proven
            or (
                result.solver_termination == "not_proven"
                and result.objective_bound is not None
                and result.relative_gap is not None
            )
        )
        for result in tenant_results
    )
    within_window = wall_seconds <= effective.batch_window_seconds
    return FullNetworkBenchmarkResult(
        config=effective,
        wall_seconds=wall_seconds,
        tenant_results=tenant_results,
        concurrent_tenant_isolation_passed=tenant_isolation,
        bounded_gap_evidence_passed=bounded_gap,
        batch_window_passed=within_window,
        launch_gate_passed=tenant_isolation and bounded_gap and within_window,
    )


__all__ = [
    "DEFAULT_BATCH_WINDOW_SECONDS",
    "FULL_NETWORK_KEY_COUNT",
    "FullNetworkBenchmarkConfig",
    "FullNetworkBenchmarkResult",
    "TenantBenchmarkResult",
    "build_full_network_benchmark_request",
    "run_full_network_benchmark",
]
