"""Pure scenario-v2 identity, wire-result, and persisted-input helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from trax_io_feature_store.schemas import LeadTimeDistribution
from trax_io_reco.contracts.context import TenantPolicyConfig
from trax_io_reco.contracts.repair import RepairPipeline

from trax_io_spine.bff.models import (
    FrontierPointWire,
    ScenarioAssumptionImpact,
    ScenarioOutcomeWire,
    ScenarioParamsWire,
    ScenarioRepairReturnOutcomeWire,
    ScenarioSolveResult,
)
from trax_io_spine.bff.scenario import (
    KeyStats,
    RepairScenarioInput,
    RepairScenarioOutcome,
    ScenarioOutcome,
    SolveResult,
)

SCENARIO_INPUTS_CONTRACT_VERSION = "scenario-inputs.v1"
SCENARIO_RESULT_CONTRACT_VERSION = "scenario-solve.v2"


def repair_scenario_input_payload(item: RepairScenarioInput) -> dict[str, Any]:
    """Serialize one immutable repair input without dropping model provenance."""

    return {
        "pn": item.pn,
        "location": item.location,
        "criticality_tier": item.criticality_tier,
        "ata_chapter": item.ata_chapter,
        "pipeline": item.pipeline.model_dump(mode="json"),
        "repair_cycle_time": (
            item.repair_cycle_time.model_dump(mode="json")
            if item.repair_cycle_time is not None
            else None
        ),
    }


def repair_scenario_input_from_payload(
    payload: Mapping[str, Any],
) -> RepairScenarioInput:
    """Hydrate and reconcile one persisted repair input."""

    pipeline = RepairPipeline.model_validate(payload.get("pipeline"))
    cycle_payload = payload.get("repair_cycle_time")
    cycle = (
        LeadTimeDistribution.model_validate(cycle_payload)
        if cycle_payload is not None
        else None
    )
    pn = str(payload.get("pn", ""))
    location = str(payload.get("location", ""))
    if not pn or not location:
        raise ValueError("persisted repair scenario input requires pn and location")
    if pipeline.part_number != pn or pipeline.location_code != location:
        raise ValueError("persisted repair pipeline does not match input key")
    if cycle is not None and (
        cycle.tenant_id != pipeline.tenant_id
        or cycle.pn != pn
        or cycle.condition != "REP"
    ):
        raise ValueError("persisted repair cycle does not match repair pipeline")
    criticality = payload.get("criticality_tier")
    if criticality is not None:
        criticality = int(criticality)
        if criticality not in range(1, 6):
            raise ValueError("persisted repair criticality tier is invalid")
    ata = payload.get("ata_chapter")
    return RepairScenarioInput(
        pn=pn,
        location=location,
        criticality_tier=criticality,
        ata_chapter=str(ata) if ata is not None else None,
        pipeline=pipeline,
        repair_cycle_time=cycle,
    )


def scenario_inputs_payload(
    *,
    source_tenant_id: str,
    source_manifest: Mapping[str, Any],
    key_universe: Sequence[tuple[str, str]],
    procurement_inputs: Sequence[KeyStats],
    repair_inputs: Sequence[RepairScenarioInput],
    tenant_policy: TenantPolicyConfig | None = None,
) -> dict[str, Any]:
    """Build the tenant snapshot needed to reproduce a scenario solve."""

    return {
        "contract_version": SCENARIO_INPUTS_CONTRACT_VERSION,
        "source_tenant_id": source_tenant_id,
        "source_manifest": dict(source_manifest),
        "key_universe": [list(key) for key in key_universe],
        "procurement_inputs": [asdict(item) for item in procurement_inputs],
        "repair_inputs": [
            repair_scenario_input_payload(item) for item in repair_inputs
        ],
        "tenant_policy": (
            tenant_policy or TenantPolicyConfig()
        ).model_dump(mode="json"),
    }


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _canonicalize(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        normalized = [_canonicalize(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ),
        )
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def scenario_input_fingerprint(
    *,
    tenant_id: str,
    source_manifest: Mapping[str, Any],
    key_universe: Sequence[tuple[str, str]],
    procurement_inputs: Sequence[KeyStats],
    repair_inputs: Sequence[RepairScenarioInput],
    params: ScenarioParamsWire,
    tenant_policy: TenantPolicyConfig | None = None,
) -> str:
    """Hash every tenant-bound, result-affecting scenario-v2 input."""

    policy = tenant_policy or TenantPolicyConfig()
    payload = _canonicalize(
        {
            "contract_version": SCENARIO_RESULT_CONTRACT_VERSION,
            "tenant_id": tenant_id,
            "source_manifest": source_manifest,
            "key_universe": key_universe,
            "procurement_inputs": procurement_inputs,
            "repair_inputs": [
                repair_scenario_input_payload(item) for item in repair_inputs
            ],
            "tenant_policy": policy.model_dump(mode="json"),
            "model_versions": {
                "scenario_solver": "bff-rq-scenario.v2",
                "repair_return": "repair-return.v1",
            },
            "repair_settings": {
                "horizon_days": 90,
                "serviceable_yield_assumption": 1.0,
                "completed_cycle_days": [],
            },
            "params": {
                "service_level_target": params.service_level_target,
                "service_level_by_tier": params.service_level_by_tier,
                "budget_cap": params.budget_cap,
                "procurement_lead_time_delta_pct": (
                    params.procurement_lead_time_delta_pct
                ),
                "repair_tat_delta_pct": params.repair_tat_delta_pct,
                "scope": params.scope.value,
                "scope_value": params.scope_value,
            },
        }
    )
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return f"scenario_v2_{hashlib.sha256(canonical.encode()).hexdigest()}"


def _outcome_wire(outcome: ScenarioOutcome) -> ScenarioOutcomeWire:
    return ScenarioOutcomeWire(
        service_level=outcome.service_level,
        projected_investment=outcome.projected_investment,
        projected_coverage=outcome.projected_coverage,
        on_hand_gap_ratio=outcome.on_hand_gap_ratio,
        scored_keys=outcome.scored_keys,
    )


def _repair_outcome_wire(
    outcome: RepairScenarioOutcome | None,
) -> ScenarioRepairReturnOutcomeWire | None:
    if outcome is None:
        return None
    return ScenarioRepairReturnOutcomeWire(
        horizon_days=outcome.horizon_days,
        eligible_quantity=outcome.eligible_quantity,
        expected_units=outcome.expected_units,
        modeled_keys=outcome.modeled_keys,
        unavailable_keys=outcome.unavailable_keys,
        unscoped_keys=outcome.unscoped_keys,
        serviceable_yield_assumption=outcome.serviceable_yield_assumption,
    )


def _scenario_impacts(
    *,
    params: ScenarioParamsWire,
    result: SolveResult,
    procurement_inputs: Sequence[KeyStats],
) -> tuple[tuple[ScenarioAssumptionImpact, ...], int]:
    procurement_ids = set(result.proposed.scored_key_ids)
    repair_ids = set(
        result.repair_proposed.modeled_key_ids
        if result.repair_proposed is not None
        else ()
    )
    impacts: list[ScenarioAssumptionImpact] = []
    affected_union: set[tuple[str, str]] = set()
    if params.service_level_target is not None:
        target_ids = procurement_ids
    elif params.service_level_by_tier:
        target_ids = {
            (key.pn, key.location)
            for key in procurement_inputs
            if (
                (key.pn, key.location) in procurement_ids
                and key.criticality_tier in params.service_level_by_tier
            )
        }
    else:
        target_ids = set()
    if target_ids:
        impacts.append(
            ScenarioAssumptionImpact(
                label="Target service level",
                affected_key_count=len(target_ids),
            )
        )
        affected_union.update(target_ids)
    if params.budget_cap is not None:
        impacts.append(
            ScenarioAssumptionImpact(
                label="Inventory budget cap",
                affected_key_count=0,
            )
        )
    if params.procurement_lead_time_delta_pct:
        impacts.append(
            ScenarioAssumptionImpact(
                label="Procurement lead time",
                affected_key_count=len(procurement_ids),
            )
        )
        affected_union.update(procurement_ids)
    if params.repair_tat_delta_pct:
        impacts.append(
            ScenarioAssumptionImpact(
                label="Repair TAT",
                affected_key_count=len(repair_ids),
            )
        )
        affected_union.update(repair_ids)
    if params.scope.value != "all" or params.scope_value is not None:
        scoped_ids = procurement_ids | repair_ids
        impacts.append(
            ScenarioAssumptionImpact(
                label="Scenario scope",
                affected_key_count=len(scoped_ids),
            )
        )
        affected_union.update(scoped_ids)
    return tuple(impacts), len(affected_union)


def _source_as_of(source_manifest: Mapping[str, Any]) -> str | None:
    raw = source_manifest.get("extract_date")
    if not isinstance(raw, str):
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        return None
    return raw


def build_scenario_result(
    *,
    tenant_id: str,
    source_manifest: Mapping[str, Any],
    key_universe: Sequence[tuple[str, str]],
    procurement_inputs: Sequence[KeyStats],
    repair_inputs: Sequence[RepairScenarioInput],
    params: ScenarioParamsWire,
    result: SolveResult,
    tenant_policy: TenantPolicyConfig | None = None,
) -> ScenarioSolveResult:
    """Build the canonical scenario-v2 response from one completed solve."""

    impacts, affected_key_count = _scenario_impacts(
        params=params,
        result=result,
        procurement_inputs=procurement_inputs,
    )
    source_coverage = (
        max(
            0.0,
            min(
                1.0,
                (result.total_keys - result.skipped_keys) / result.total_keys,
            ),
        )
        if result.total_keys
        else 0.0
    )
    source_as_of = _source_as_of(source_manifest)
    warning_codes = {
        "scenario_uniform_rq_approximation",
        "scenario_repair_serviceable_yield_unobserved",
    }
    if source_as_of is None:
        warning_codes.add("scenario_source_as_of_unavailable")
    if result.skipped_keys:
        warning_codes.add("scenario_procurement_inputs_incomplete")
    if result.repair_proposed is not None and result.repair_proposed.unavailable_keys:
        warning_codes.add("scenario_rep_evidence_unavailable")
    if result.repair_proposed is not None and result.repair_proposed.unscoped_keys:
        warning_codes.add("scenario_repair_scope_metadata_unavailable")
    if any(
        item.pipeline.eligible_quantity > 0
        and item.repair_cycle_time is not None
        and not item.repair_cycle_time.observed_cycle_days
        for item in repair_inputs
    ):
        warning_codes.add("scenario_rep_fallback_right_censoring_not_fitted")

    return ScenarioSolveResult(
        params=params,
        current=_outcome_wire(result.current),
        proposed=_outcome_wire(result.proposed),
        delta_investment=result.delta_investment,
        delta_coverage=result.delta_coverage,
        frontier=tuple(
            FrontierPointWire(
                service_level=point.service_level,
                projected_investment=point.projected_investment,
                projected_coverage=point.projected_coverage,
            )
            for point in result.frontier
        ),
        skipped_keys=result.skipped_keys,
        total_keys=result.total_keys,
        budget_cap_binds=result.budget_cap_binds,
        contract_version=SCENARIO_RESULT_CONTRACT_VERSION,
        repair_current=_repair_outcome_wire(result.repair_current),
        repair_proposed=_repair_outcome_wire(result.repair_proposed),
        assumption_impacts=impacts,
        affected_key_count=affected_key_count,
        fingerprint=scenario_input_fingerprint(
            tenant_id=tenant_id,
            source_manifest=source_manifest,
            key_universe=key_universe,
            procurement_inputs=procurement_inputs,
            repair_inputs=repair_inputs,
            params=params,
            tenant_policy=tenant_policy,
        ),
        source_as_of=source_as_of,
        source_coverage=source_coverage,
        source_confidence=source_coverage,
        warning_codes=tuple(sorted(warning_codes)),
    )


__all__ = [
    "SCENARIO_INPUTS_CONTRACT_VERSION",
    "SCENARIO_RESULT_CONTRACT_VERSION",
    "build_scenario_result",
    "repair_scenario_input_from_payload",
    "repair_scenario_input_payload",
    "scenario_input_fingerprint",
    "scenario_inputs_payload",
]
