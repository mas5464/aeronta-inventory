"""Finite-domain property tests for Phase-1 time and reconciliation invariants.

These exhaustively enumerate small domains instead of sampling example dates or
quantities, so every boundary relation in the selected ranges is checked.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import product

from trax_io_feature_store.schemas import (
    DemandHistory,
    DemandObservation,
    OpenOrder,
    OpenOrdersSnapshot,
)

from trax_io_reco.contracts.context import (
    NetPosition,
    NetPositionMember,
    ScheduledDemandItem,
)
from trax_io_reco.contracts.enums import EvidenceKind
from trax_io_reco.demand.basis import (
    demand_basis_trace,
    scheduled_units_in_horizon,
)
from trax_io_reco.position.net_position import (
    open_receipts_in_horizon,
    rollup_net,
)

AS_OF = date(2026, 4, 17)


def test_property_scheduled_demand_uses_closed_horizon_for_all_small_offsets() -> None:
    for horizon_days in range(0, 8):
        items = tuple(
            ScheduledDemandItem(
                due_date=AS_OF + timedelta(days=offset),
                qty=quantity,
                source_ref=f"{offset}:{quantity}",
                source_kind=EvidenceKind.REQUISITION,
            )
            for offset, quantity in product(range(-2, 11), range(1, 4))
        )
        expected = sum(
            item.qty
            for item in items
            if AS_OF <= item.due_date <= AS_OF + timedelta(days=horizon_days)
        )

        assert (
            scheduled_units_in_horizon(
                items,
                as_of=AS_OF,
                horizon_days=horizon_days,
            )
            == expected
        )


def test_property_open_receipts_include_every_due_or_overdue_open_line() -> None:
    for horizon_days in range(0, 8):
        orders = [
            OpenOrder(
                order_id=f"{offset}:{quantity}",
                order_type="PO",
                qty_open=quantity,
                expected_rcv_date=AS_OF + timedelta(days=offset),
            )
            for offset, quantity in product(range(-2, 11), range(1, 4))
        ]
        orders.append(
            OpenOrder(
                order_id="undated",
                order_type="PO",
                qty_open=99,
                expected_rcv_date=None,
            )
        )
        snapshot = OpenOrdersSnapshot(
            tenant_id="acme",
            pn="P",
            location="L",
            snapshot_at=datetime(2026, 4, 17),
            orders=orders,
            total_open_qty=sum(order.qty_open for order in orders),
            extract_date=AS_OF,
        )
        trace = open_receipts_in_horizon(
            snapshot,
            as_of=AS_OF,
            horizon_days=horizon_days,
        )
        expected_due = sum(
            order.qty_open
            for order in orders
            if order.expected_rcv_date is not None
            and order.expected_rcv_date <= AS_OF + timedelta(days=horizon_days)
        )
        expected_overdue = sum(
            order.qty_open
            for order in orders
            if order.expected_rcv_date is not None and order.expected_rcv_date < AS_OF
        )

        assert trace.open_receipts_due == expected_due
        assert trace.overdue_open_receipts_due == expected_overdue


def test_property_event_counts_never_inherit_multi_unit_quantities() -> None:
    for removal_units, issue_units, removal_events, issue_events in product(
        range(0, 5),
        range(0, 5),
        range(0, 3),
        range(0, 3),
    ):
        history = DemandHistory(
            tenant_id="acme",
            pn="P",
            location="L",
            observation_start=AS_OF,
            observation_end=AS_OF,
            bucket="day",
            event_count_source="observed",
            observations=[
                DemandObservation(
                    bucket="day",
                    period_start=AS_OF,
                    removals=removal_units,
                    issues=issue_units,
                    removal_events=removal_events,
                    issue_events=issue_events,
                )
            ],
            extract_date=AS_OF,
        )
        trace = demand_basis_trace(history)

        assert trace.demanded_units == removal_units + issue_units
        assert trace.demand_event_count == removal_events + issue_events


def test_property_rollup_is_exact_sum_of_canonical_member_ledgers() -> None:
    for available_a, available_b, demand_a, demand_b, receipt_a, receipt_b in product(
        range(0, 3),
        repeat=6,
    ):
        positions = []
        for pn, available, demand, receipts in (
            ("A", available_a, demand_a, receipt_a),
            ("B", available_b, demand_b, receipt_b),
        ):
            net = float(available + receipts - demand)
            member = NetPositionMember(
                pn=pn,
                location="L",
                projection_kind="EMPIRICAL",
                projected_historical_demand=float(demand),
                projected_demand=float(demand),
                available=float(available),
                open_receipts_in_window=float(receipts),
                expected_receipts_in_window=float(receipts),
                net=net,
                scheduled_demand_status="available",
                open_receipts_status="available",
            )
            positions.append(
                NetPosition(
                    pn=pn,
                    location="L",
                    group_id="G",
                    window_days=30,
                    available=float(available),
                    expected_receipts_in_window=float(receipts),
                    projected_demand=float(demand),
                    net=net,
                    shortage=max(0.0, -net),
                    projected_historical_demand=float(demand),
                    open_receipts_in_window=float(receipts),
                    member_contributions=(member,),
                    scheduled_demand_status="available",
                    open_receipts_status="available",
                )
            )

        rolled = rollup_net(positions)

        assert rolled.available == sum(position.available for position in positions)
        assert rolled.expected_receipts_in_window == sum(
            position.expected_receipts_in_window for position in positions
        )
        assert rolled.projected_demand == sum(position.projected_demand for position in positions)
        assert rolled.net == (
            rolled.available + rolled.expected_receipts_in_window - rolled.projected_demand
        )
        assert rolled.shortage == max(0.0, -rolled.net)
        assert [(member.pn, member.location) for member in rolled.member_contributions] == [
            ("A", "L"),
            ("B", "L"),
        ]
