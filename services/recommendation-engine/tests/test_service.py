from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from trax_io_feature_store import InMemoryFeatureStore, TenantContext
from trax_io_feature_store.schemas import DemandHistory, DemandObservation

import trax_io_reco.candidate.service as candidate_service
from tests.fixtures.builders import interchange, seed_part
from trax_io_reco.contracts.context import (
    DemandProjection,
    ScheduledDemandItem,
    TenantPolicyConfig,
)
from trax_io_reco.contracts.enums import EvidenceKind, RecommendationType, Regime
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.service import RecommendationService

TENANT = TenantContext(tenant_id="acme")
NOW = datetime(2026, 4, 17, 9, 0, 0)


class _FittedStatisticalProjector:
    """Test double for a fitted projector whose rate differs from raw history."""

    def __init__(self, rates: dict[str, float]) -> None:
        self._rates = rates

    def project(self, *, context, regime: Regime) -> DemandProjection:
        rate = self._rates[context.pn]
        return DemandProjection(
            mean_per_day=rate,
            std_per_day=rate**0.5,
            dist_kind="COMPOUND_POISSON",
            dist_params={"lambda": rate, "clump_p": 1.0},
            historical_component=rate,
            scheduled_component=0.0,
            scheduled_demand_total=float(sum(item.qty for item in context.scheduled_demand)),
            scheduled_by_date={item.due_date: float(item.qty) for item in context.scheduled_demand},
            basis_window_days=365,
        )


def _service_with_shortage(
    *,
    observed_empty_schedule: bool = False,
    config: TenantPolicyConfig | None = None,
) -> RecommendationService:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P-100",
        location="YYZ",
        monthly_units=[20] * 12,
        serviceable=2,
        lead_mean_days=60.0,
        current_policy=(5, 5, 2, 40),
    )
    if observed_empty_schedule:
        inv.seed("acme", "scheduled_demand", ("P-100", "YYZ"), ())
    return RecommendationService(
        feature_store=fs,
        inventory_state=inv,
        config=config,
    )


def test_service_emits_purchase_for_shortage() -> None:
    svc = _service_with_shortage()
    batch = svc.run(tenant=TENANT, keys=[("P-100", "YYZ")], now=NOW)
    assert batch.summary.total >= 1
    types = {r.type for r in batch.recommendations}
    assert RecommendationType.PURCHASE in types
    for r in batch.recommendations:
        assert r.description and r.reason and r.supporting_evidence
        assert 0.0 <= r.confidence_score <= 1.0


def test_observed_empty_schedule_is_not_scored_as_a_missing_stub() -> None:
    unavailable = _service_with_shortage().run(
        tenant=TENANT,
        keys=[("P-100", "YYZ")],
        now=NOW,
    )
    observed_empty = _service_with_shortage(observed_empty_schedule=True).run(
        tenant=TENANT,
        keys=[("P-100", "YYZ")],
        now=NOW,
    )

    unavailable_purchase = next(
        rec for rec in unavailable.recommendations if rec.type == RecommendationType.PURCHASE
    )
    observed_empty_purchase = next(
        rec for rec in observed_empty.recommendations if rec.type == RecommendationType.PURCHASE
    )

    assert observed_empty_purchase.confidence_score > unavailable_purchase.confidence_score


def test_service_is_deterministic() -> None:
    svc = _service_with_shortage()
    b1 = svc.run(tenant=TENANT, keys=[("P-100", "YYZ")], now=NOW)
    b2 = svc.run(tenant=TENANT, keys=[("P-100", "YYZ")], now=NOW)
    # input_snapshot_hash and ordering/fields identical modulo recommendation_id.
    h1 = [r.input_snapshot_hash for r in b1.recommendations]
    h2 = [r.input_snapshot_hash for r in b2.recommendations]
    assert h1 == h2 and len(h1) >= 1
    assert [r.type for r in b1.recommendations] == [r.type for r in b2.recommendations]


def test_candidate_preview_is_deterministic_and_reconciles_final_actions() -> None:
    svc = _service_with_shortage()
    first = svc.run_with_frontiers(
        tenant=TENANT,
        keys=[("P-100", "YYZ")],
        now=NOW,
        as_of=NOW.date(),
    )
    second = svc.run_with_frontiers(
        tenant=TENANT,
        keys=[("P-100", "YYZ")],
        now=NOW + timedelta(hours=1),
        as_of=NOW.date(),
    )

    assert first.frontiers == second.frontiers
    frontier = first.frontiers[0]
    assert frontier.decision_key == "P-100@YYZ"
    assert sum(candidate.is_no_change for candidate in frontier.candidates) == 1
    baseline = next(candidate for candidate in frontier.candidates if candidate.is_no_change)
    purchases = [
        candidate
        for candidate in frontier.candidates
        if candidate.candidate_kind == "purchase"
    ]
    assert purchases
    assert baseline.lifecycle_costs.acquisition_cash == 0
    assert all(
        candidate.reconciliation.acquisition_cash
        == candidate.lifecycle_costs.acquisition_cash
        == (
            candidate.reconciliation.purchase_quantity
            * Decimal("100")
        )
        for candidate in purchases
    )
    assert all(candidate.outcome.expected_shortage == 0 for candidate in purchases)
    assert any(candidate.feasible for candidate in purchases)
    assert any(
        not candidate.feasible
        and "delta_gt_100pct" in candidate.infeasibility_reasons
        for candidate in frontier.candidates
        if any(action.kind == "adjust_policy" for action in candidate.actions)
    )
    assert frontier.candidates[0].is_no_change


def test_candidate_preview_reports_actual_served_model_identity() -> None:
    preview = _service_with_shortage().run_with_frontiers(
        tenant=TENANT,
        keys=[("P-100", "YYZ")],
        now=NOW,
    )

    identities = {candidate.model_identity for candidate in preview.frontiers[0].candidates}
    assert len(identities) == 1
    identity = identities.pop()
    assert identity.forecast_model == "historical-compound-poisson"
    assert identity.forecast_version == "historical-scheduled-v1"
    assert identity.policy_model == "s_S"
    assert identity.policy_version == "deterministic-v1"


def test_candidate_fingerprint_changes_with_result_affecting_config() -> None:
    default = _service_with_shortage().run_with_frontiers(
        tenant=TENANT,
        keys=[("P-100", "YYZ")],
        now=NOW,
    )
    changed = _service_with_shortage(
        config=TenantPolicyConfig(holding_cost_rate=0.4)
    ).run_with_frontiers(
        tenant=TENANT,
        keys=[("P-100", "YYZ")],
        now=NOW,
    )

    assert (
        default.frontiers[0].frontier_fingerprint
        != changed.frontiers[0].frontier_fingerprint
    )


def test_candidate_fingerprint_versions_result_affecting_scoring(
    monkeypatch,
) -> None:
    default = _service_with_shortage().run_with_frontiers(
        tenant=TENANT,
        keys=[("P-100", "YYZ")],
        now=NOW,
    )
    monkeypatch.setattr(
        candidate_service,
        "AOG_RISK_MODEL_VERSION",
        "aog-risk-test-v2",
    )
    changed = _service_with_shortage().run_with_frontiers(
        tenant=TENANT,
        keys=[("P-100", "YYZ")],
        now=NOW,
    )

    assert (
        default.frontiers[0].frontier_fingerprint
        != changed.frontiers[0].frontier_fingerprint
    )


def test_eligible_key_without_recommendation_still_gets_no_change_candidate() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P-STABLE",
        location="YYZ",
        monthly_units=[0] * 12,
        serviceable=1,
        current_policy=(0, 1, 0, 1),
        scheduled=[],
    )

    preview = RecommendationService(feature_store=fs, inventory_state=inv).run_with_frontiers(
        tenant=TENANT,
        keys=[("P-STABLE", "YYZ")],
        now=NOW,
    )

    assert preview.recommendation_batch.recommendations == ()
    assert len(preview.frontiers) == 1
    assert len(preview.frontiers[0].candidates) == 1
    assert preview.frontiers[0].candidates[0].is_no_change


def test_service_skips_missing_key() -> None:
    svc = _service_with_shortage()
    batch = svc.run(tenant=TENANT, keys=[("P-100", "YYZ"), ("MISSING", "ZZZ")], now=NOW)
    assert any(s.pn == "MISSING" for s in batch.skipped)


def test_reserved_stock_is_not_exposed_as_transferable_donor_excess() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P-RESERVED",
        location="YYZ",
        monthly_units=[20] * 12,
        serviceable=0,
        lead_mean_days=60.0,
        current_policy=(5, 5, 2, 40),
    )
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P-RESERVED",
        location="YOW",
        monthly_units=[0] * 12,
        serviceable=50,
        allocated=50,
        current_policy=(2, 2, 1, 10),
    )

    batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
        tenant=TENANT,
        keys=[("P-RESERVED", "YYZ"), ("P-RESERVED", "YOW")],
        now=NOW,
    )

    assert not any(
        recommendation.type == RecommendationType.TRANSFER
        and recommendation.current_location == "YYZ"
        for recommendation in batch.recommendations
    )


def test_service_skips_unavailable_legacy_history_instead_of_treating_it_as_zero() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P-UNKNOWN",
        location="YYZ",
        monthly_units=[],
        serviceable=100,
        unit_cost="8000",
        current_policy=(2, 2, 1, 10),
    )
    fs.seed(
        "acme",
        "demand_history",
        ("P-UNKNOWN", "YYZ"),
        DemandHistory(
            tenant_id="acme",
            pn="P-UNKNOWN",
            location="YYZ",
            observations=[],
            extract_date=date(2026, 4, 1),
        ),
    )

    batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
        tenant=TENANT,
        keys=[("P-UNKNOWN", "YYZ")],
        now=NOW,
    )

    assert batch.recommendations == ()
    assert batch.skipped[0].reason == "demand_history_unavailable"


def test_service_treats_configured_empty_interval_as_genuine_zero_demand() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P-ZERO",
        location="YYZ",
        monthly_units=[],
        serviceable=100,
        unit_cost="8000",
        current_policy=(2, 2, 1, 10),
        scheduled=[],
    )
    fs.seed(
        "acme",
        "demand_history",
        ("P-ZERO", "YYZ"),
        DemandHistory(
            tenant_id="acme",
            pn="P-ZERO",
            location="YYZ",
            observation_start=date(2023, 4, 1),
            observation_end=date(2026, 4, 1),
            bucket="month",
            event_count_source="observed",
            observations=[
                DemandObservation(
                    bucket="month",
                    period_start=date(2023, 4, 1),
                    removal_events=0,
                    issue_events=0,
                )
            ],
            extract_date=date(2026, 4, 1),
        ),
    )

    batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
        tenant=TENANT,
        keys=[("P-ZERO", "YYZ")],
        now=NOW,
    )

    assert not batch.skipped
    assert any(
        recommendation.type == RecommendationType.SELL for recommendation in batch.recommendations
    )


def test_service_preserves_fitted_statistical_projection_in_calculation_evidence() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    # Six historical event buckets classify as intermittent, while the injected
    # fitted rate is intentionally unlike the raw six-units-per-year average.
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P-FITTED",
        location="YYZ",
        monthly_units=[0] * 6 + [1] * 6,
        serviceable=0,
        lead_mean_days=60.0,
        current_policy=(1, 1, 0, 2),
    )
    fitted_rate = 0.375

    batch = RecommendationService(
        feature_store=fs,
        inventory_state=inv,
        projector=_FittedStatisticalProjector({"P-FITTED": fitted_rate}),
    ).run(
        tenant=TENANT,
        keys=[("P-FITTED", "YYZ")],
        now=NOW,
    )

    purchase = next(
        recommendation
        for recommendation in batch.recommendations
        if recommendation.type == RecommendationType.PURCHASE
    )
    evidence = purchase.calculation_evidence
    assert evidence is not None
    assert evidence.projection_kind == "COMPOUND_POISSON"
    assert evidence.served_historical_per_day == fitted_rate
    assert evidence.projected_historical_demand == (fitted_rate * purchase.horizon_days)
    assert evidence.projected_demand == purchase.projected_demand
    assert evidence.members[0].projected_historical_demand == (fitted_rate * purchase.horizon_days)


def test_pooled_interchange_calculation_members_sum_exactly() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    members = ("P-POOL-A", "P-POOL-B")
    rates = {"P-POOL-A": 0.5, "P-POOL-B": 0.25}
    schedules = {
        "P-POOL-A": [
            ScheduledDemandItem(
                due_date=date(2026, 5, 17),
                qty=3,
                source_ref="A-END",
                source_kind=EvidenceKind.TASK_CARD,
            )
        ],
        "P-POOL-B": [
            ScheduledDemandItem(
                due_date=NOW.date(),
                qty=2,
                source_ref="B-TODAY",
                source_kind=EvidenceKind.TASK_CARD,
            )
        ],
    }
    for pn, serviceable, allocated, open_qty in (
        ("P-POOL-A", 1, 0, 2),
        ("P-POOL-B", 2, 1, 1),
    ):
        seed_part(
            fs,
            inv,
            tenant_id="acme",
            pn=pn,
            location="YYZ",
            monthly_units=[0] * 6 + [1] * 6,
            serviceable=serviceable,
            allocated=allocated,
            lead_mean_days=30.0,
            current_policy=(1, 1, 0, 2),
            open_qty=open_qty,
            open_rcv_date=date(2026, 5, 17),
            scheduled=schedules[pn],
        )
        fs.seed(
            "acme",
            "interchangeable_graph",
            (pn,),
            interchange(
                tenant_id="acme",
                pn=pn,
                group_id="POOL-EXACT",
                members=list(members),
                edges=[
                    ("P-POOL-A", "P-POOL-B", False),
                    ("P-POOL-B", "P-POOL-A", False),
                ],
            ),
        )

    batch = RecommendationService(
        feature_store=fs,
        inventory_state=inv,
        projector=_FittedStatisticalProjector(rates),
    ).run(
        tenant=TENANT,
        keys=[(pn, "YYZ") for pn in members],
        now=NOW,
    )

    purchase = next(
        recommendation
        for recommendation in batch.recommendations
        if recommendation.type == RecommendationType.PURCHASE
    )
    evidence = purchase.calculation_evidence
    assert evidence is not None
    assert evidence.pooled_group_id == "POOL-EXACT"
    assert {member.pn for member in evidence.members} == set(members)

    summed_fields = (
        "projected_historical_demand",
        "scheduled_demand_due",
        "projected_demand",
        "dispatchable_available",
        "open_receipts_due",
        "overdue_open_receipts_due",
        "repair_receipts_due",
        "expected_receipts_due",
        "net_position",
    )
    for field in summed_fields:
        assert getattr(evidence, field) == sum(
            getattr(member, field) for member in evidence.members
        )
    for member in evidence.members:
        assert member.projected_demand == (
            member.projected_historical_demand + member.scheduled_demand_due
        )
        assert member.expected_receipts_due == (
            member.open_receipts_due + member.repair_receipts_due
        )
        assert member.net_position == (
            member.dispatchable_available + member.expected_receipts_due - member.projected_demand
        )

    by_pn = {member.pn: member for member in evidence.members}
    assert by_pn["P-POOL-A"].model_dump() == {
        "pn": "P-POOL-A",
        "location": "YYZ",
        "projection_kind": "COMPOUND_POISSON",
        "projected_historical_demand": 15.0,
        "scheduled_demand_due": 3.0,
        "projected_demand": 18.0,
        "dispatchable_available": 1.0,
        "open_receipts_due": 2.0,
        "overdue_open_receipts_due": 0.0,
        "repair_receipts_due": 0.0,
        "expected_receipts_due": 2.0,
        "net_position": -15.0,
        "scheduled_demand_status": "available",
        "scheduled_demand_undated_lines": 0,
        "scheduled_demand_undated_units": 0,
        "open_receipts_status": "available",
        "open_receipts_undated_lines": 0,
        "open_receipts_undated_units": 0,
    }
    assert by_pn["P-POOL-B"].model_dump() == {
        "pn": "P-POOL-B",
        "location": "YYZ",
        "projection_kind": "COMPOUND_POISSON",
        "projected_historical_demand": 7.5,
        "scheduled_demand_due": 2.0,
        "projected_demand": 9.5,
        "dispatchable_available": 1.0,
        "open_receipts_due": 1.0,
        "overdue_open_receipts_due": 0.0,
        "repair_receipts_due": 0.0,
        "expected_receipts_due": 1.0,
        "net_position": -7.5,
        "scheduled_demand_status": "available",
        "scheduled_demand_undated_lines": 0,
        "scheduled_demand_undated_units": 0,
        "open_receipts_status": "available",
        "open_receipts_undated_lines": 0,
        "open_receipts_undated_units": 0,
    }


def _pooled_purchase_for_order(
    keys: list[tuple[str, str]],
    *,
    second_member_serviceable: int = 2,
):
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    members = ("P-ORDER-A", "P-ORDER-B")
    for pn, serviceable in (
        ("P-ORDER-A", 1),
        ("P-ORDER-B", second_member_serviceable),
    ):
        seed_part(
            fs,
            inv,
            tenant_id="acme",
            pn=pn,
            location="YYZ",
            monthly_units=[0] * 6 + [1] * 6,
            serviceable=serviceable,
            lead_mean_days=30.0,
            current_policy=(1, 1, 0, 2),
        )
        fs.seed(
            "acme",
            "interchangeable_graph",
            (pn,),
            interchange(
                tenant_id="acme",
                pn=pn,
                group_id="POOL-ORDER",
                members=list(members),
                edges=[
                    ("P-ORDER-A", "P-ORDER-B", False),
                    ("P-ORDER-B", "P-ORDER-A", False),
                ],
            ),
        )
    batch = RecommendationService(
        feature_store=fs,
        inventory_state=inv,
        projector=_FittedStatisticalProjector({"P-ORDER-A": 0.5, "P-ORDER-B": 0.25}),
    ).run(tenant=TENANT, keys=keys, now=NOW)
    return next(
        recommendation
        for recommendation in batch.recommendations
        if recommendation.type == RecommendationType.PURCHASE
    )


def test_pooled_evidence_and_identity_are_key_order_invariant() -> None:
    forward = _pooled_purchase_for_order([("P-ORDER-A", "YYZ"), ("P-ORDER-B", "YYZ")])
    reversed_order = _pooled_purchase_for_order([("P-ORDER-B", "YYZ"), ("P-ORDER-A", "YYZ")])

    assert forward.calculation_evidence == reversed_order.calculation_evidence
    assert forward.input_snapshot_hash == reversed_order.input_snapshot_hash
    assert [
        (member.pn, member.location)
        for member in forward.calculation_evidence.members  # type: ignore[union-attr]
    ] == [("P-ORDER-A", "YYZ"), ("P-ORDER-B", "YYZ")]


def test_nonrepresentative_pool_member_changes_action_identity() -> None:
    baseline = _pooled_purchase_for_order([("P-ORDER-A", "YYZ"), ("P-ORDER-B", "YYZ")])
    changed = _pooled_purchase_for_order(
        [("P-ORDER-A", "YYZ"), ("P-ORDER-B", "YYZ")],
        second_member_serviceable=9,
    )

    assert baseline.input_snapshot_hash != changed.input_snapshot_hash
    assert baseline.calculation_evidence != changed.calculation_evidence


def test_partial_interchange_worklist_is_disclosed_not_presented_as_complete() -> None:
    purchase = _pooled_purchase_for_order([("P-ORDER-A", "YYZ")])
    evidence = purchase.calculation_evidence

    assert evidence is not None
    assert evidence.pooling_scope == "worklist_partial"
    assert evidence.pooled_group_id == "POOL-ORDER"
    assert evidence.excluded_member_keys == ("P-ORDER-B@YYZ",)
    assert [(member.pn, member.location) for member in evidence.members] == [("P-ORDER-A", "YYZ")]


def test_business_offset_as_of_is_independent_from_utc_generation_date() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    local_now = datetime.fromisoformat("2026-04-01T23:30:00-04:00")
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P-TZ",
        location="YYZ",
        monthly_units=[30] * 12,
        serviceable=0,
        lead_mean_days=30.0,
        current_policy=(1, 1, 0, 2),
        scheduled=[
            ScheduledDemandItem(
                due_date=date(2026, 4, 1),
                qty=5,
                source_ref="LOCAL-DUE",
                source_kind=EvidenceKind.REQUISITION,
            )
        ],
    )

    batch = RecommendationService(
        feature_store=fs,
        inventory_state=inv,
    ).run(
        tenant=TENANT,
        keys=[("P-TZ", "YYZ")],
        now=local_now,
    )
    purchase = next(
        recommendation
        for recommendation in batch.recommendations
        if recommendation.type == RecommendationType.PURCHASE
    )
    evidence = purchase.calculation_evidence

    assert evidence is not None
    assert evidence.as_of == date(2026, 4, 1)
    assert evidence.scheduled_demand_due == 5
    assert purchase.generated_at.isoformat() == "2026-04-02T03:30:00+00:00"
    assert batch.generated_at == purchase.generated_at
