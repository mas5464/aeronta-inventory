"""Property / invariant tests across all scenarios (spec §9.3)."""

from __future__ import annotations

from datetime import datetime

import pytest
from trax_io_feature_store import TenantContext

from tests.fixtures import scenarios
from trax_io_reco.contracts.enums import RecommendationType
from trax_io_reco.service import RecommendationService

NOW = datetime(2026, 4, 17, 9, 0, 0)
_EXCESS = {RecommendationType.REDUCE_STOCK, RecommendationType.SELL}
_SHORT = {RecommendationType.PURCHASE, RecommendationType.TRANSFER}


def _run(scenario_fn):
    fs, inv, tenant_id, keys = scenario_fn()
    return RecommendationService(feature_store=fs, inventory_state=inv).run(
        tenant=TenantContext(tenant_id=tenant_id), keys=keys, now=NOW
    )


def _without_id(rec) -> str:
    return rec.model_copy(update={"recommendation_id": "X"}).model_dump_json()


@pytest.mark.parametrize("scenario_fn", scenarios.ALL_SCENARIOS)
def test_recommendation_field_invariants(scenario_fn) -> None:
    for r in _run(scenario_fn).recommendations:
        assert r.description and r.reason and r.supporting_evidence
        assert 0.0 <= r.confidence_score <= 1.0
        assert r.shortage_quantity >= 0.0
        if r.type == RecommendationType.TRANSFER:
            assert r.recommended_location is not None
        if r.policy is not None:
            assert r.policy.rop >= r.policy.safety_stock
            assert r.policy.max_stock >= r.policy.rop + r.policy.eoq


@pytest.mark.parametrize("scenario_fn", scenarios.ALL_SCENARIOS)
def test_no_contradictory_recommendations_per_key(scenario_fn) -> None:
    by_key: dict[tuple[str, str], set] = {}
    for r in _run(scenario_fn).recommendations:
        by_key.setdefault((r.part_number, r.current_location), set()).add(r.type)
    for types in by_key.values():
        assert not (types & _EXCESS and types & _SHORT), f"contradiction: {types}"


@pytest.mark.parametrize("scenario_fn", scenarios.ALL_SCENARIOS)
def test_determinism_modulo_ids(scenario_fn) -> None:
    b1, b2 = _run(scenario_fn), _run(scenario_fn)
    assert [_without_id(r) for r in b1.recommendations] == [
        _without_id(r) for r in b2.recommendations
    ]


@pytest.mark.parametrize("scenario_fn", scenarios.ALL_SCENARIOS)
def test_summary_matches_recommendations(scenario_fn) -> None:
    batch = _run(scenario_fn)
    assert batch.summary.total == len(batch.recommendations)
    assert sum(batch.summary.by_type.values()) == len(batch.recommendations)
