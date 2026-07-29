"""Regression tests for the adversarial-code-review findings (over-buy, one-way
interchange, hash invariance, hazmat cap, AOG 72h window, constraint-skip, apportion,
unit_cost validation, ranking criticality, repair-return boundary)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from trax_io_feature_store import InMemoryFeatureStore, TenantContext
from trax_io_feature_store.schemas import (
    InterchangeableGraph,
    InterchangeEdge,
    PartAttributes,
)

from tests.fixtures.builders import interchange, seed_part
from tests.test_scoring import _net, _rec
from trax_io_reco.contracts.context import AogSignal, CurrentPolicy, RepairTat
from trax_io_reco.contracts.enums import AutonomyTier, RecommendationType
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.policy.constraints import apply_constraints
from trax_io_reco.position.net_position import apportion, expected_receipts, two_way_members
from trax_io_reco.ranking import rank
from trax_io_reco.risk.aog import AogRiskScorer
from trax_io_reco.service import RecommendationService

TENANT = TenantContext(tenant_id="acme")
NOW = datetime(2026, 4, 17, 9, 0, 0)
AS_OF = date(2026, 4, 17)


def _run(fs, inv, keys, **kw):
    return RecommendationService(feature_store=fs, inventory_state=inv).run(
        tenant=TENANT, keys=keys, now=NOW, **kw
    )


# --- CRITICAL: interchange group both-short must NOT over-buy (one buy for the group) --- #
def test_both_short_group_buys_once_not_n_times() -> None:
    fs, inv = InMemoryFeatureStore(), InMemoryInventoryState()
    members = ["P-GA", "P-GB"]
    edges = [("P-GA", "P-GB", False), ("P-GB", "P-GA", False)]
    for pn in members:
        seed_part(fs, inv, tenant_id="acme", pn=pn, location="YYZ", monthly_units=[20] * 12,
                  serviceable=0, lead_mean_days=60.0, current_policy=(5, 5, 2, 40), tier=3)
        fs.seed("acme", "interchangeable_graph", (pn,),
                interchange(tenant_id="acme", pn=pn, group_id="GG", members=members, edges=edges))
    batch = _run(fs, inv, [("P-GA", "YYZ"), ("P-GB", "YYZ")])
    purchases = [r for r in batch.recommendations if r.type == RecommendationType.PURCHASE]
    assert len(purchases) == 1, "pooled group shortage must be bought once, not per member"


# --- one-way interchange must NOT suppress the dependent part's purchase --- #
def test_two_way_members_excludes_one_way_only() -> None:
    g = InterchangeableGraph(
        tenant_id="t", pn="P-A", group_id="G", members=["P-A", "P-B"],
        edges=[InterchangeEdge(from_pn="P-A", to_pn="P-B", one_way=True)],
        extract_date=date(2026, 4, 1),
    )
    assert two_way_members(g) == ["P-A"]  # one-way partner excluded from the rollup set


def test_one_way_partner_does_not_suppress_purchase() -> None:
    fs, inv = InMemoryFeatureStore(), InMemoryInventoryState()
    members = ["P-OA", "P-OB"]
    edges = [("P-OA", "P-OB", True)]  # one-way only
    seed_part(fs, inv, tenant_id="acme", pn="P-OA", location="YYZ", monthly_units=[20] * 12,
              serviceable=0, lead_mean_days=60.0, current_policy=(5, 5, 2, 40), tier=3)
    seed_part(fs, inv, tenant_id="acme", pn="P-OB", location="YYZ", monthly_units=[0] * 12,
              serviceable=50, current_policy=(5, 5, 2, 20), tier=3)
    for pn in members:
        fs.seed("acme", "interchangeable_graph", (pn,),
                interchange(tenant_id="acme", pn=pn, group_id="GO", members=members, edges=edges))
    batch = _run(fs, inv, [("P-OA", "YYZ"), ("P-OB", "YYZ")])
    p_oa = [r for r in batch.recommendations
            if r.part_number == "P-OA" and r.type == RecommendationType.PURCHASE]
    assert p_oa, "one-way partner stock must not suppress P-OA's purchase"


# --- input_snapshot_hash invariant to Decimal scale --- #
def test_hash_invariant_to_decimal_scale() -> None:
    def _ctx(unit_cost: str):
        fs, inv = InMemoryFeatureStore(), InMemoryInventoryState()
        seed_part(fs, inv, tenant_id="acme", pn="P", location="L", monthly_units=[1, 1],
                  unit_cost=unit_cost, serviceable=1)
        return ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
            tenant=TENANT, pn="P", location="L"
        )

    assert RecommendationService._hash(_ctx("100")) == RecommendationService._hash(_ctx("100.00"))


# --- negative/zero unit_cost routes to skipped --- #
def test_zero_unit_cost_skipped() -> None:
    fs, inv = InMemoryFeatureStore(), InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", monthly_units=[5, 5], unit_cost="0",
              serviceable=0)
    batch = _run(fs, inv, [("P", "L")])
    assert any(s.reason == "invalid_unit_cost" for s in batch.skipped)
    assert batch.recommendations == ()


# --- hazmat / tool-control 2x Max cap --- #
def _pa(**kw):
    return PartAttributes(tenant_id="t", pn="P", extract_date=date(2026, 4, 1), **kw)


def test_hazmat_cap_clamps_at_2x() -> None:
    cur = CurrentPolicy(rop=8, eoq=5, safety_stock=3, max_stock=10)
    res = apply_constraints((8, 5, 3, 40), part_attributes=_pa(hazardous_material=True),
                            current_policy=cur, avg_daily_demand=0.1, min_order_qty=1)
    assert res.values is not None and res.values[3] == 20  # clamped to 2 x 10
    assert "hazmat_tool_capped" in res.flags


def test_hazmat_no_clamp_at_or_below_2x() -> None:
    cur = CurrentPolicy(rop=8, eoq=5, safety_stock=3, max_stock=10)
    res = apply_constraints((8, 5, 3, 20), part_attributes=_pa(tool_control_item=True),
                            current_policy=cur, avg_daily_demand=0.1, min_order_qty=1)
    assert res.values is not None and res.values[3] == 20
    assert "hazmat_tool_capped" not in res.flags


# --- AOG 72h reactivation window --- #
def _ctx_for_aog(**kw):
    fs, inv = InMemoryFeatureStore(), InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", monthly_units=[1], tier=4, **kw)
    return ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )


def test_aog_72h_window_inclusive_and_exclusive() -> None:
    within = _ctx_for_aog(aog=AogSignal(last_shortage_at=NOW - timedelta(hours=71)))
    outside = _ctx_for_aog(aog=AogSignal(last_shortage_at=NOW - timedelta(hours=73)))
    rec = _rec(RecommendationType.PURCHASE, shortage=2, qty=2)
    in_scored = AogRiskScorer().score(rec, context=within, net=_net(2))
    out_scored = AogRiskScorer().score(rec, context=outside, net=_net(2))
    assert in_scored.suggested_autonomy_tier == AutonomyTier.ADVISOR
    assert "active_aog" in in_scored.guardrail_flags
    assert "active_aog" not in out_scored.guardrail_flags


# --- constraint-violation routes to skipped through the full service --- #
def test_constraint_violation_skipped_end_to_end() -> None:
    fs, inv = InMemoryFeatureStore(), InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", monthly_units=[30] * 12,
              shelf_life_days=1, serviceable=5, tier=4)
    batch = _run(fs, inv, [("P", "L")])
    assert any(s.reason.startswith("policy:") for s in batch.skipped)
    assert not any(r.part_number == "P" for r in batch.recommendations)


# --- apportion never over-allocates with a zero-consumption member --- #
def test_apportion_zero_consumption_member() -> None:
    out = apportion((100, 10, 20, 200), members=["A", "B", "C"],
                    trailing_consumption={"A": 50.0, "B": 50.0, "C": 0.0})
    assert out["C"] == (0, 0, 0, 0)  # zero consumption -> zero share
    assert sum(out[m][0] for m in ("A", "B", "C")) <= 100  # rop shares never exceed group total


# --- aggregate repair stock is not identity-aware future supply --- #
def test_aggregate_repair_stock_gets_no_future_receipt_credit_at_any_boundary() -> None:
    fs, inv = InMemoryFeatureStore(), InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", monthly_units=[1], in_repair=5,
              repair_tat=RepairTat(mean_days=20.0, p90_days=30.0, n_observations=6))
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )
    inc = expected_receipts(open_orders=ctx.open_orders, repair_tat=ctx.repair_tat,
                            stock_position=ctx.stock_position, window_days=30, as_of=AS_OF)
    exc = expected_receipts(open_orders=ctx.open_orders, repair_tat=ctx.repair_tat,
                            stock_position=ctx.stock_position, window_days=29, as_of=AS_OF)
    assert inc == 0.0 and exc == 0.0


# --- ranking weights criticality --- #
def test_ranking_prefers_criticality_over_cost() -> None:
    high_crit_low_cost = _rec(RecommendationType.PURCHASE, cost="100", part="A").model_copy(
        update={"criticality_tier": 1}
    )
    low_crit_high_cost = _rec(RecommendationType.PURCHASE, cost="400", part="B").model_copy(
        update={"criticality_tier": 5}
    )
    ranked = rank([low_crit_high_cost, high_crit_low_cost])
    assert ranked[0].criticality_tier == 1  # tier-1 weight 5 x 100 = 500 > tier-5 1 x 400 = 400
