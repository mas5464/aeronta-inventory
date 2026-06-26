from __future__ import annotations

from datetime import date, datetime

from trax_io_feature_store import InMemoryFeatureStore, TenantContext

from tests.fixtures.builders import seed_part
from trax_io_reco.contracts.enums import RecommendationType, Regime
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.demand.projection import HistoricalScheduledProjector
from trax_io_reco.policy.mini_engine import MiniPolicyEngine
from trax_io_reco.position.net_position import net_position
from trax_io_reco.recommenders.adjust_min_max import AdjustMinMaxRecommender
from trax_io_reco.recommenders.base import DonorOption, RecommenderInput
from trax_io_reco.recommenders.purchase import PurchaseRecommender
from trax_io_reco.recommenders.reduce_sell import ReduceSellRecommender
from trax_io_reco.recommenders.transfer import TransferRecommender

TENANT = TenantContext(tenant_id="acme")
NOW = datetime(2026, 4, 17, 9, 0, 0)
AS_OF = date(2026, 4, 17)


def make_input(*, regime, donors=None, reporting=30, **seed_kw) -> RecommenderInput:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", **seed_kw)
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )
    proj = HistoricalScheduledProjector().project(context=ctx, regime=regime)
    policy = MiniPolicyEngine().recommend(context=ctx, regime=regime, projection=proj)
    assert isinstance(policy, PolicyRecommendation)
    return RecommenderInput(
        context=ctx, projection=proj, policy=policy, now=NOW, as_of=AS_OF,
        input_snapshot_hash="h", reporting_horizon_days=reporting,
        net_position=lambda w: net_position(context=ctx, projection=proj, window_days=w, as_of=AS_OF),
        donor_lookup=lambda pn, gid, mwh: (donors or []),
    )


def test_scenario1_demand_exceeds_stock_purchase() -> None:
    inp = make_input(regime=Regime.HIGH_VOLUME, monthly_units=[20] * 12, serviceable=2,
                     lead_mean_days=90.0, current_policy=(5, 5, 2, 40))
    recs = PurchaseRecommender().propose(inp)
    assert len(recs) == 1 and recs[0].type == RecommendationType.PURCHASE
    assert recs[0].shortage_quantity > 0 and recs[0].recommended_quantity > 0
    assert recs[0].horizon_days == 90  # protection period, not the 30d reporting window


def test_scenario6_open_po_covers_suppresses_purchase() -> None:
    inp = make_input(regime=Regime.HIGH_VOLUME, monthly_units=[20] * 12, serviceable=2,
                     lead_mean_days=90.0, current_policy=(5, 5, 2, 40),
                     open_qty=80, open_rcv_date=date(2026, 5, 1))
    assert PurchaseRecommender().propose(inp) == []


def test_scenario2_transfer_preferred() -> None:
    donors = [DonorOption(location="YOW", serviceable_excess=10, lead_days=3.0, cost=0.0)]
    inp = make_input(regime=Regime.HIGH_VOLUME, monthly_units=[20] * 12, serviceable=0,
                     lead_mean_days=90.0, current_policy=(5, 5, 2, 40), donors=donors)
    recs = TransferRecommender().propose(inp)
    assert len(recs) == 1 and recs[0].type == RecommendationType.TRANSFER
    assert recs[0].recommended_location == "YOW"


def test_scenario3_high_value_unused_reduce_or_sell() -> None:
    inp = make_input(regime=Regime.ULTRA_RARE, monthly_units=[0] * 12, serviceable=100,
                     unit_cost="8000", current_policy=(2, 2, 1, 10))
    recs = ReduceSellRecommender().propose(inp)
    assert len(recs) == 1
    assert recs[0].type in (RecommendationType.SELL, RecommendationType.REDUCE_STOCK)
    assert recs[0].estimated_cost_impact < 0  # holding released = savings


def test_scenario4_adjust_min_max() -> None:
    inp = make_input(regime=Regime.HIGH_VOLUME, monthly_units=[30] * 12, serviceable=5,
                     current_policy=(1, 1, 0, 2))
    recs = AdjustMinMaxRecommender().propose(inp)
    assert len(recs) == 1 and recs[0].type == RecommendationType.ADJUST_MIN_MAX
    assert recs[0].policy is not None and recs[0].current_policy is not None
    assert recs[0].policy.max_stock != recs[0].current_policy.max_stock
