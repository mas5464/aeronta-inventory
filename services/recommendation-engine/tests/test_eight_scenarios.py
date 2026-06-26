"""The eight required acceptance scenarios (spec §9.1), each asserted through the full
RecommendationService. Every scenario also asserts description is populated."""

from __future__ import annotations

from datetime import datetime

import pytest
from trax_io_feature_store import TenantContext

from tests.fixtures import scenarios
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType
from trax_io_reco.service import RecommendationService

NOW = datetime(2026, 4, 17, 9, 0, 0)


def _run(scenario_fn):
    fs, inv, tenant_id, keys = scenario_fn()
    batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
        tenant=TenantContext(tenant_id=tenant_id), keys=keys, now=NOW
    )
    return batch


def _types_at(batch, location):
    return {r.type for r in batch.recommendations if r.current_location == location}


@pytest.mark.parametrize("scenario_fn", scenarios.ALL_SCENARIOS)
def test_every_recommendation_has_description(scenario_fn) -> None:
    batch = _run(scenario_fn)
    for r in batch.recommendations:
        assert r.description, f"empty description in {scenario_fn.__name__}"


def test_scenario_1_demand_exceeds_stock() -> None:
    batch = _run(scenarios.scenario_1_demand_exceeds_stock)
    purchases = [r for r in batch.recommendations if r.type == RecommendationType.PURCHASE]
    assert len(purchases) == 1
    assert purchases[0].shortage_quantity > 0
    assert purchases[0].recommended_quantity >= purchases[0].shortage_quantity
    assert purchases[0].horizon_days == 60  # protection period, not the 30d reporting window


def test_scenario_2_transfer_better_than_purchase() -> None:
    batch = _run(scenarios.scenario_2_transfer_better)
    yyz = [r for r in batch.recommendations if r.current_location == "YYZ"]
    transfers = [r for r in yyz if r.type == RecommendationType.TRANSFER]
    assert len(transfers) == 1
    assert transfers[0].recommended_location == "YOW"
    assert RecommendationType.PURCHASE not in _types_at(batch, "YYZ")


def test_scenario_3_high_value_unused() -> None:
    batch = _run(scenarios.scenario_3_high_value_unused)
    rec = [r for r in batch.recommendations
           if r.type in (RecommendationType.SELL, RecommendationType.REDUCE_STOCK)]
    assert len(rec) == 1
    # zero usage + no scheduled demand + high value -> SELL (not just REDUCE_STOCK).
    assert rec[0].type == RecommendationType.SELL
    assert rec[0].estimated_cost_impact < 0  # holding released = savings


def test_scenario_4_min_max_adjustment() -> None:
    batch = _run(scenarios.scenario_4_min_max_adjustment)
    adjust = [r for r in batch.recommendations if r.type == RecommendationType.ADJUST_MIN_MAX]
    assert len(adjust) == 1
    assert adjust[0].policy is not None and adjust[0].current_policy is not None
    assert adjust[0].policy.max_stock != adjust[0].current_policy.max_stock


def test_scenario_5_interchangeable_no_over_buy() -> None:
    batch = _run(scenarios.scenario_5_interchangeable_available)
    # P-5A is short alone, but its two-way partner P-5B at YYZ holds ample stock:
    # the group rollup means NO purchase for P-5A.
    p5a = [r for r in batch.recommendations
           if r.part_number == "P-5A" and r.type == RecommendationType.PURCHASE]
    assert p5a == []


def test_scenario_6_open_po_covers() -> None:
    batch = _run(scenarios.scenario_6_open_po_covers)
    assert RecommendationType.PURCHASE not in {r.type for r in batch.recommendations}


def test_scenario_7_location_specific_shortage() -> None:
    batch = _run(scenarios.scenario_7_location_specific_shortage)
    yyz_actions = _types_at(batch, "YYZ")
    yow_actions = _types_at(batch, "YOW")
    assert yyz_actions & {RecommendationType.PURCHASE, RecommendationType.TRANSFER}
    assert not (yow_actions & {RecommendationType.PURCHASE, RecommendationType.TRANSFER})


def test_scenario_8_long_tat_creates_aog_risk() -> None:
    batch = _run(scenarios.scenario_8_long_tat_aog)
    risky = [r for r in batch.recommendations if r.aog_risk_level >= AogRiskLevel.HIGH]
    assert risky, "expected a HIGH/CRITICAL AOG risk recommendation"
    # Assert against the PURCHASE rec specifically (EXPEDITE only applies to Purchase/Transfer).
    purchases = [r for r in risky if r.type == RecommendationType.PURCHASE]
    assert purchases, "expected a high-risk PURCHASE for the shorted rotable"
    assert purchases[0].suggested_autonomy_tier == AutonomyTier.ADVISOR
    assert "EXPEDITE" in purchases[0].reason
    # No non-Purchase/Transfer rec should ever carry EXPEDITE.
    for r in batch.recommendations:
        if r.type not in (RecommendationType.PURCHASE, RecommendationType.TRANSFER):
            assert "EXPEDITE" not in r.reason
