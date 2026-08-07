from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from trax_io_feature_store.schemas import LeadTimeDistribution

from trax_io_reco.position.repair_pipeline import build_repair_pipeline
from trax_io_reco.repair_returns import project_repair_returns

AS_OF = date(2026, 7, 28)


def _pipeline(
    *,
    age_days: int = 0,
    quantity: int = 1,
    aggregate_wip: int | None = None,
):
    opened = AS_OF - timedelta(days=age_days)
    order = SimpleNamespace(
        order_id="RO-1",
        order_line_id="1",
        order_type="RO",
        vendor="SHOP-1",
        shop="SHOP-1",
        qty_open=quantity,
        expected_rcv_date=None,
        opened_at=opened.isoformat(),
        status="OPEN",
        serial_number=None,
        location="MIA",
    )
    return build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=SimpleNamespace(orders=[order]),
        aggregate_wip_quantity=(
            quantity if aggregate_wip is None else aggregate_wip
        ),
        as_of=AS_OF,
    )


def _pipeline_with_ages(*ages: int):
    orders = [
        SimpleNamespace(
            order_id=f"RO-{index}",
            order_line_id="1",
            order_type="RO",
            vendor="SHOP-1",
            shop="SHOP-1",
            qty_open=1,
            expected_rcv_date=None,
            opened_at=(AS_OF - timedelta(days=age)).isoformat(),
            status="OPEN",
            serial_number=f"SER-{index}",
            location="MIA",
        )
        for index, age in enumerate(ages, start=1)
    ]
    return build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=SimpleNamespace(orders=orders),
        aggregate_wip_quantity=len(orders),
        as_of=AS_OF,
    )


def _repair_distribution(
    *,
    p50: float = 20,
    p90: float = 40,
    promised: float | None = 30,
    observed: bool = True,
) -> LeadTimeDistribution:
    return LeadTimeDistribution(
        tenant_id="acme",
        pn="PN-1",
        vendor="SHOP-1",
        condition="REP",
        promised_lead_days=promised,
        realized_mean_days=25 if observed else 0,
        realized_p50_days=p50 if observed else 0,
        realized_p90_days=p90 if observed else 0,
        realized_p99_days=60 if observed else 0,
        promised_vs_actual_delta_mean=5 if observed else None,
        n_observations=20 if observed else 0,
        extract_date=AS_OF,
        evidence_status="observed" if observed else "configured_fallback",
        source=(
            "order_plan_closed_orders" if observed else "pn_vendor_price"
        ),
        grouping_level="part_vendor_condition",
        confidence="medium" if observed else "low",
        data_cutoff=AS_OF,
        model_version="supply-cycle.v1",
        proxy_definition=(
            "order_creation_to_last_receipt"
            if observed
            else "configured_repair_promise"
        ),
        classification_source=(
            "explicit_order_type" if observed else "configured_condition"
        ),
    )


def test_kaplan_meier_returns_are_bounded_monotone_and_right_censored() -> None:
    profile = project_repair_returns(
        pipeline=_pipeline_with_ages(0, 20),
        horizons=[30, 0, 15, 15],
        completed_cycle_days=[10, 30],
    )

    assert [horizon.horizon_days for horizon in profile.horizons] == [0, 15, 30]
    assert profile.evidence.method == "kaplan_meier"
    assert profile.evidence.completed_observations == 2
    assert profile.evidence.right_censored_observations == 2
    expected = [horizon.expected_units for horizon in profile.horizons]
    assert expected == sorted(expected)
    for horizon in profile.horizons:
        assert 0 <= horizon.p10_units <= horizon.expected_units
        assert horizon.expected_units <= horizon.p90_units <= 2
        assert 0 <= horizon.mean_serviceable_probability <= 1

    # The age-20 open unit remains in the risk set at the day-10 event. The
    # age-zero unit therefore has P(return by day 15)=1/3, not the completed-only
    # empirical 1/2: current WIP contributed right-censored evidence.
    age_zero = next(
        item
        for item in profile.horizons[1].item_probabilities
        if item.age_days == 0
    )
    assert age_zero.return_probability == pytest.approx(1 / 3)


def test_current_age_conditions_residual_life_instead_of_restarting_clock() -> None:
    configured = _repair_distribution(
        p50=0,
        p90=0,
        promised=20,
        observed=False,
    )

    profile = project_repair_returns(
        pipeline=_pipeline(age_days=15),
        horizons=[4, 5],
        repair_cycle_time=configured,
    )

    assert profile.evidence.method == "deterministic_promise"
    assert profile.horizons[0].expected_units == 0
    assert profile.horizons[1].expected_units == 1
    assert profile.horizons[1].item_probabilities[0].return_probability == 1
    assert "repair_return_configured_promise" in profile.warning_codes


def test_slower_repair_tat_weakly_decreases_fixed_horizon_returns() -> None:
    pipeline = _pipeline(age_days=5, quantity=4)
    distribution = _repair_distribution()

    baseline = project_repair_returns(
        pipeline=pipeline,
        horizons=[20],
        repair_cycle_time=distribution,
        tat_multiplier=1.0,
    )
    slower = project_repair_returns(
        pipeline=pipeline,
        horizons=[20],
        repair_cycle_time=distribution,
        tat_multiplier=1.5,
    )

    assert baseline.evidence.method == "lognormal_quantile"
    assert slower.horizons[0].expected_units <= baseline.horizons[0].expected_units

    empirical_baseline = project_repair_returns(
        pipeline=pipeline,
        horizons=[20],
        completed_cycle_days=[8, 14, 30],
        tat_multiplier=1.0,
    )
    empirical_slower = project_repair_returns(
        pipeline=pipeline,
        horizons=[20],
        completed_cycle_days=[8, 14, 30],
        tat_multiplier=2.0,
    )
    assert empirical_slower.horizons[0].expected_units <= (
        empirical_baseline.horizons[0].expected_units
    )


def test_serviceable_yield_bounds_expected_units_and_is_disclosed() -> None:
    profile = project_repair_returns(
        pipeline=_pipeline(quantity=5),
        horizons=[60],
        repair_cycle_time=_repair_distribution(),
        serviceable_yield=0.6,
    )

    assert profile.evidence.serviceable_yield == 0.6
    assert profile.horizons[0].expected_units <= 3
    item = profile.horizons[0].item_probabilities[0]
    assert item.serviceable_probability == item.return_probability * 0.6


def test_procurement_distribution_cannot_supply_repair_return_evidence() -> None:
    procurement = _repair_distribution().model_copy(
        update={
            "condition": "NEW",
            "proxy_definition": None,
        }
    )

    profile = project_repair_returns(
        pipeline=_pipeline(quantity=2),
        horizons=[90],
        repair_cycle_time=procurement,
    )

    assert profile.status == "unavailable"
    assert profile.evidence.method == "unavailable"
    assert profile.horizons[0].expected_units == 0
    assert profile.warning_codes == ("repair_return_evidence_unavailable",)

    legacy_rep = LeadTimeDistribution(
        tenant_id="acme",
        pn="PN-1",
        vendor="DEFAULT",
        condition="REP",
        promised_lead_days=20,
        realized_mean_days=20,
        realized_p50_days=20,
        realized_p90_days=30,
        realized_p99_days=40,
        n_observations=5,
        extract_date=AS_OF,
    )
    legacy_profile = project_repair_returns(
        pipeline=_pipeline(quantity=2),
        horizons=[90],
        repair_cycle_time=legacy_rep,
    )
    assert legacy_profile.status == "unavailable"
    assert legacy_profile.evidence.method == "unavailable"


def test_excluded_and_aggregate_residual_work_never_receive_return_credit() -> None:
    pipeline = _pipeline(quantity=4, aggregate_wip=2)
    profile = project_repair_returns(
        pipeline=pipeline,
        horizons=[90],
        completed_cycle_days=[10, 20, 30],
    )

    assert pipeline.eligible_quantity == 2
    assert profile.eligible_quantity == 2
    assert profile.excluded_quantity == 2
    assert profile.horizons[0].expected_units <= 2
    assert sum(
        item.quantity for item in profile.horizons[0].item_probabilities
    ) == 2


def test_return_profile_counts_missing_identity_exclusions_once() -> None:
    def order(order_id: str, quantity: int) -> SimpleNamespace:
        return SimpleNamespace(
            order_id=order_id,
            order_line_id="1",
            order_type="RO",
            vendor="SHOP-1",
            shop="SHOP-1",
            qty_open=quantity,
            expected_rcv_date=None,
            opened_at="2026-07-01",
            status="OPEN",
            serial_number=None,
            location="MIA",
        )

    pipeline = build_repair_pipeline(
        tenant_id="acme",
        part_number="PN-1",
        location_code="MIA",
        open_orders=SimpleNamespace(
            orders=[order("?", 2), order("RO-VALID", 1)]
        ),
        aggregate_wip_quantity=5,
        as_of=AS_OF,
    )
    profile = project_repair_returns(
        pipeline=pipeline,
        horizons=[30],
        completed_cycle_days=[10, 20, 30],
    )

    assert profile.eligible_quantity == 1
    assert profile.excluded_quantity == 2
    assert profile.aggregate_residual_quantity == 2
    assert (
        profile.eligible_quantity
        + profile.excluded_quantity
        + profile.aggregate_residual_quantity
        == pipeline.aggregate_wip_quantity
    )
