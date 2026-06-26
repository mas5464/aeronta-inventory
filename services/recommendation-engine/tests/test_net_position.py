from __future__ import annotations

from datetime import date

from trax_io_feature_store import InMemoryFeatureStore, TenantContext

from tests.fixtures.builders import seed_part
from trax_io_reco.contracts.context import RepairTat, StockPosition
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.demand.projection import HistoricalScheduledProjector
from trax_io_reco.position.net_position import (
    apportion,
    available,
    expected_receipts,
    net_position,
)

TENANT = TenantContext(tenant_id="acme")
AS_OF = date(2026, 4, 17)


def test_available_excludes_in_repair_rental_loan() -> None:
    sp = StockPosition(
        on_hand=20, serviceable=10, allocated_reserved=2, unserviceable_in_repair=5,
        rental=3, loan=1,
    )
    assert available(sp) == 8.0  # 10 - 2; the rest is not dispatchable


def test_receipt_outside_window_excluded() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", monthly_units=[1, 1],
              open_qty=5, open_rcv_date=date(2026, 9, 1))  # ~135 days out
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )
    r_short = expected_receipts(open_orders=ctx.open_orders, repair_tat=ctx.repair_tat,
                                stock_position=ctx.stock_position, window_days=30, as_of=AS_OF)
    r_long = expected_receipts(open_orders=ctx.open_orders, repair_tat=ctx.repair_tat,
                               stock_position=ctx.stock_position, window_days=180, as_of=AS_OF)
    assert r_short == 0.0  # receipt 135d out is excluded from a 30d window
    assert r_long == 5.0   # included within 180d


def test_repair_returns_not_double_counted_with_open_orders() -> None:
    # open orders 4 due soon + 3 repair returns due soon = 7 (a sum, not a double-count)
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", monthly_units=[1],
              open_qty=4, open_rcv_date=date(2026, 4, 20), in_repair=3,
              repair_tat=RepairTat(mean_days=5.0, p90_days=10.0, n_observations=5))
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )
    r = expected_receipts(open_orders=ctx.open_orders, repair_tat=ctx.repair_tat,
                          stock_position=ctx.stock_position, window_days=30, as_of=AS_OF)
    assert r == 7.0


def test_net_identity_and_shortage() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", monthly_units=[30] * 12,
              serviceable=2)
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )
    proj = HistoricalScheduledProjector().project(context=ctx, regime=Regime.HIGH_VOLUME)
    np_ = net_position(context=ctx, projection=proj, window_days=30, as_of=AS_OF)
    assert np_.net == np_.available + np_.expected_receipts_in_window - np_.projected_demand
    assert np_.shortage == max(0.0, -np_.net)
    assert np_.shortage > 0  # 2 on hand vs ~30/mo demand


def test_apportion_proportional_to_consumption() -> None:
    out = apportion((10, 4, 6, 14), members=["A", "B"], trailing_consumption={"A": 75.0, "B": 25.0})
    assert out["A"][0] > out["B"][0]  # A consumes more -> larger ROP share
    assert out["A"] == (8, 3, 5, 11) or out["A"][0] == round(10 * 0.75)
