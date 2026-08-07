from __future__ import annotations

from datetime import date, datetime

from trax_io_feature_store import InMemoryFeatureStore, TenantContext
from trax_io_feature_store.schemas import (
    OpenOrder,
    OpenOrdersSnapshot,
    RequisitionLine,
    RequisitionSnapshot,
)

from tests.fixtures.builders import seed_part
from trax_io_reco.contracts.context import RepairTat, ScheduledDemandItem, StockPosition
from trax_io_reco.contracts.enums import EvidenceKind, Regime
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.demand.projection import HistoricalScheduledProjector
from trax_io_reco.position.net_position import (
    apportion,
    available,
    expected_receipts,
    net_position,
    open_receipts_in_horizon,
)

TENANT = TenantContext(tenant_id="acme")
AS_OF = date(2026, 4, 17)


def test_available_excludes_in_repair_rental_loan() -> None:
    sp = StockPosition(
        on_hand=20,
        serviceable=10,
        allocated_reserved=2,
        unserviceable_in_repair=5,
        rental=3,
        loan=1,
    )
    assert available(sp) == 8.0  # 10 - 2; the rest is not dispatchable


def test_receipt_outside_window_excluded() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P",
        location="L",
        monthly_units=[1, 1],
        open_qty=5,
        open_rcv_date=date(2026, 9, 1),
    )  # ~135 days out
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )
    r_short = expected_receipts(
        open_orders=ctx.open_orders,
        repair_tat=ctx.repair_tat,
        stock_position=ctx.stock_position,
        window_days=30,
        as_of=AS_OF,
    )
    r_long = expected_receipts(
        open_orders=ctx.open_orders,
        repair_tat=ctx.repair_tat,
        stock_position=ctx.stock_position,
        window_days=180,
        as_of=AS_OF,
    )
    assert r_short == 0.0  # receipt 135d out is excluded from a 30d window
    assert r_long == 5.0  # included within 180d


def test_aggregate_in_repair_is_not_credited_as_future_receipt() -> None:
    # Phase 1 has no stable repair-order identity. The 3 aggregate in-repair units
    # therefore cannot be credited as future supply; only the dated open order counts.
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P",
        location="L",
        monthly_units=[1],
        open_qty=4,
        open_rcv_date=date(2026, 4, 20),
        in_repair=3,
        repair_tat=RepairTat(mean_days=5.0, p90_days=10.0, n_observations=5),
    )
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )
    r = expected_receipts(
        open_orders=ctx.open_orders,
        repair_tat=ctx.repair_tat,
        stock_position=ctx.stock_position,
        window_days=30,
        as_of=AS_OF,
    )
    projection = HistoricalScheduledProjector().project(
        context=ctx,
        regime=Regime.INTERMITTENT,
    )
    position = net_position(
        context=ctx,
        projection=projection,
        window_days=30,
        as_of=AS_OF,
    )

    assert r == 4.0
    assert position.open_receipts_in_window == 4.0
    assert position.repair_receipts_in_window == 0.0
    assert position.expected_receipts_in_window == 4.0


def test_net_identity_and_shortage() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs, inv, tenant_id="acme", pn="P", location="L", monthly_units=[30] * 12, serviceable=2
    )
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )
    proj = HistoricalScheduledProjector().project(context=ctx, regime=Regime.HIGH_VOLUME)
    np_ = net_position(context=ctx, projection=proj, window_days=30, as_of=AS_OF)
    assert np_.net == np_.available + np_.expected_receipts_in_window - np_.projected_demand
    assert np_.shortage == max(0.0, -np_.net)
    assert np_.shortage > 0  # 2 on hand vs ~30/mo demand


def test_scheduled_demand_is_included_only_in_its_inclusive_horizon() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    scheduled = [
        ScheduledDemandItem(
            due_date=AS_OF,
            qty=2,
            source_ref="TODAY",
            source_kind=EvidenceKind.TASK_CARD,
        ),
        ScheduledDemandItem(
            due_date=date(2026, 5, 17),
            qty=3,
            source_ref="BOUNDARY",
            source_kind=EvidenceKind.TASK_CARD,
        ),
        ScheduledDemandItem(
            due_date=date(2026, 5, 18),
            qty=100,
            source_ref="OUTSIDE",
            source_kind=EvidenceKind.TASK_CARD,
        ),
    ]
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P",
        location="L",
        monthly_units=[30] * 12,
        scheduled=scheduled,
    )
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT,
        pn="P",
        location="L",
    )
    projection = HistoricalScheduledProjector().project(
        context=ctx,
        regime=Regime.HIGH_VOLUME,
    )

    today = net_position(
        context=ctx,
        projection=projection,
        window_days=0,
        as_of=AS_OF,
    )
    thirty_days = net_position(
        context=ctx,
        projection=projection,
        window_days=30,
        as_of=AS_OF,
    )

    assert today.projected_demand == 2.0
    assert thirty_days.projected_demand == projection.historical_component * 30 + 5


def test_open_receipt_trace_includes_boundary_and_discloses_overdue() -> None:
    snapshot = OpenOrdersSnapshot(
        tenant_id="acme",
        pn="P",
        location="L",
        snapshot_at=date(2026, 4, 17),
        orders=[
            OpenOrder(
                order_id="LATE",
                order_type="PO",
                qty_open=4,
                expected_rcv_date=date(2026, 4, 10),
            ),
            OpenOrder(
                order_id="BOUNDARY",
                order_type="PO",
                qty_open=3,
                expected_rcv_date=date(2026, 5, 17),
            ),
            OpenOrder(
                order_id="OUTSIDE",
                order_type="PO",
                qty_open=5,
                expected_rcv_date=date(2026, 5, 18),
            ),
        ],
        total_open_qty=12,
        extract_date=AS_OF,
    )

    receipt_trace = open_receipts_in_horizon(
        snapshot,
        as_of=AS_OF,
        horizon_days=30,
    )

    assert receipt_trace.open_receipts_due == 7
    assert receipt_trace.overdue_open_receipts_due == 4


def test_known_empty_sources_are_distinct_from_unavailable_sources() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P",
        location="L",
        monthly_units=[1],
    )
    unavailable = (
        ContextAssembler(
            features=FeatureReader(fs),
            inventory_state=inv,
        )
        .assemble(tenant=TENANT, pn="P", location="L")
        .model_copy(update={"open_orders": None})
    )
    projection = HistoricalScheduledProjector().project(
        context=unavailable,
        regime=Regime.INTERMITTENT,
    )
    unavailable_position = net_position(
        context=unavailable,
        projection=projection,
        window_days=30,
        as_of=AS_OF,
    )

    inv.seed("acme", "scheduled_demand", ("P", "L"), ())
    fs.seed(
        "acme",
        "open_orders_snapshot",
        ("P", "L"),
        OpenOrdersSnapshot(
            tenant_id="acme",
            pn="P",
            location="L",
            snapshot_at=datetime(2026, 4, 17),
            orders=[],
            total_open_qty=0,
            extract_date=AS_OF,
        ),
    )
    available = ContextAssembler(
        features=FeatureReader(fs),
        inventory_state=inv,
    ).assemble(tenant=TENANT, pn="P", location="L")
    available_projection = HistoricalScheduledProjector().project(
        context=available,
        regime=Regime.INTERMITTENT,
    )
    available_position = net_position(
        context=available,
        projection=available_projection,
        window_days=30,
        as_of=AS_OF,
    )

    assert unavailable_position.scheduled_demand_status == "unavailable"
    assert unavailable_position.open_receipts_status == "unavailable"
    assert available_position.scheduled_demand_status == "available"
    assert available_position.open_receipts_status == "available"
    assert unavailable_position.scheduled_demand_in_window == 0
    assert available_position.scheduled_demand_in_window == 0
    assert unavailable_position.open_receipts_in_window == 0
    assert available_position.open_receipts_in_window == 0


def test_undated_sources_are_partial_and_preserve_excluded_quantities() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P",
        location="L",
        monthly_units=[1],
    )
    inv.seed("acme", "scheduled_demand", ("P", "L"), ())
    fs.seed(
        "acme",
        "requisition_snapshot",
        ("P", "L"),
        RequisitionSnapshot(
            tenant_id="acme",
            pn="P",
            location="L",
            snapshot_at=datetime(2026, 4, 17),
            lines=[
                RequisitionLine(
                    requisition_id="REQ-UNDATED",
                    qty_needed=7,
                    need_by=None,
                )
            ],
            total_qty_needed=7,
            extract_date=AS_OF,
        ),
    )
    fs.seed(
        "acme",
        "open_orders_snapshot",
        ("P", "L"),
        OpenOrdersSnapshot(
            tenant_id="acme",
            pn="P",
            location="L",
            snapshot_at=datetime(2026, 4, 17),
            orders=[
                OpenOrder(
                    order_id="PO-UNDATED",
                    order_type="PO",
                    qty_open=5,
                    expected_rcv_date=None,
                )
            ],
            total_open_qty=5,
            extract_date=AS_OF,
        ),
    )
    context = ContextAssembler(
        features=FeatureReader(fs),
        inventory_state=inv,
    ).assemble(tenant=TENANT, pn="P", location="L")
    projection = HistoricalScheduledProjector().project(
        context=context,
        regime=Regime.INTERMITTENT,
    )
    position = net_position(
        context=context,
        projection=projection,
        window_days=30,
        as_of=AS_OF,
    )

    assert position.scheduled_demand_status == "partial"
    assert position.scheduled_demand_undated_lines == 1
    assert position.scheduled_demand_undated_units == 7
    assert position.open_receipts_status == "partial"
    assert position.open_receipts_undated_lines == 1
    assert position.open_receipts_undated_units == 5
    assert position.scheduled_demand_in_window == 0
    assert position.open_receipts_in_window == 0


def test_undated_receipt_evidence_counts_procurement_only() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(
        fs,
        inv,
        tenant_id="acme",
        pn="P",
        location="L",
        monthly_units=[1],
    )
    fs.seed(
        "acme",
        "open_orders_snapshot",
        ("P", "L"),
        OpenOrdersSnapshot(
            tenant_id="acme",
            pn="P",
            location="L",
            snapshot_at=datetime(2026, 4, 17),
            orders=[
                OpenOrder(
                    order_id="PO-DATED",
                    order_type="PO",
                    qty_open=3,
                    expected_rcv_date=date(2026, 4, 20),
                ),
                OpenOrder(
                    order_id="RO-UNDATED",
                    order_type="RO",
                    qty_open=5,
                    expected_rcv_date=None,
                ),
            ],
            total_open_qty=8,
            extract_date=AS_OF,
        ),
    )
    context = ContextAssembler(
        features=FeatureReader(fs),
        inventory_state=inv,
    ).assemble(tenant=TENANT, pn="P", location="L")
    projection = HistoricalScheduledProjector().project(
        context=context,
        regime=Regime.INTERMITTENT,
    )

    position = net_position(
        context=context,
        projection=projection,
        window_days=30,
        as_of=AS_OF,
    )

    assert position.open_receipts_in_window == 3
    assert position.open_receipts_status == "available"
    assert position.open_receipts_undated_lines == 0
    assert position.open_receipts_undated_units == 0


def test_apportion_proportional_to_consumption() -> None:
    out = apportion((10, 4, 6, 14), members=["A", "B"], trailing_consumption={"A": 75.0, "B": 25.0})
    assert out["A"][0] > out["B"][0]  # A consumes more -> larger ROP share
    assert out["A"] == (8, 3, 5, 11) or out["A"][0] == round(10 * 0.75)
