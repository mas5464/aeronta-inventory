"""Net-position calculator — the shared primitive every recommender keys off (spec §5.5).

available = max(0, serviceable - allocated_reserved)   (in-repair / rental / loan excluded)
expected_receipts = open orders due in window  +  repair returns due in window  (disjoint sources)
net = available + expected_receipts - projected_demand
shortage = max(0, -net)
"""

from __future__ import annotations

from datetime import date, timedelta

from trax_io_feature_store.schemas import InterchangeableGraph, OpenOrdersSnapshot

from trax_io_reco.contracts.context import (
    DemandProjection,
    NetPosition,
    PartLocationContext,
    RepairTat,
    StockPosition,
)


def available(stock_position: StockPosition) -> float:
    """Dispatchable stock. Excludes in-repair (not serviceable), rental and loan
    (borrowed liabilities)."""
    return float(max(0, stock_position.serviceable - stock_position.allocated_reserved))


def expected_receipts(
    *,
    open_orders: OpenOrdersSnapshot | None,
    repair_tat: RepairTat,
    stock_position: StockPosition,
    window_days: int,
    as_of: date,
) -> float:
    """Inbound quantity expected within the window. Open POs/ROs and in-house repair
    returns are disjoint sources (no physical unit appears in both)."""
    horizon = as_of + timedelta(days=window_days)
    total = 0.0
    if open_orders is not None:
        for o in open_orders.orders:
            if o.expected_rcv_date is not None and o.expected_rcv_date <= horizon:
                total += o.qty_open
    # Repair-loop returns: in-house units expected serviceable within the window.
    if (
        repair_tat.n_observations > 0
        and repair_tat.p90_days <= window_days
        and stock_position.unserviceable_in_repair > 0
    ):
        total += float(stock_position.unserviceable_in_repair)
    return total


def net_position(
    *,
    context: PartLocationContext,
    projection: DemandProjection,
    window_days: int,
    as_of: date,
) -> NetPosition:
    avail = available(context.stock_position)
    receipts = expected_receipts(
        open_orders=context.open_orders,
        repair_tat=context.repair_tat,
        stock_position=context.stock_position,
        window_days=window_days,
        as_of=as_of,
    )
    demand = projection.mean_per_day * window_days
    net = avail + receipts - demand
    gid = context.interchange_group.group_id if context.interchange_group else None
    return NetPosition(
        pn=context.pn,
        location=context.location,
        group_id=gid,
        window_days=window_days,
        available=avail,
        expected_receipts_in_window=receipts,
        projected_demand=demand,
        net=net,
        shortage=max(0.0, -net),
    )


def two_way_members(graph: InterchangeableGraph) -> list[str]:
    """Members mutually substitutable via two-way (non one-way) edges, plus the head PN.
    One-way edges are directional and excluded from the demand-rollup set."""
    members = {graph.pn}
    for e in graph.edges:
        if not e.one_way:
            members.add(e.from_pn)
            members.add(e.to_pn)
    # Fall back to the declared member list ONLY when there are no edges at all.
    # (When the graph has only one-way edges, the rollup set must stay {pn} — a one-way
    # partner's stock must not suppress this part's demand.)
    if not graph.edges and graph.members:
        members.update(graph.members)
    return sorted(members)


def rollup_net(member_positions: list[NetPosition]) -> NetPosition:
    """Aggregate net position across interchange-group members (spec §7.6)."""
    avail = sum(p.available for p in member_positions)
    receipts = sum(p.expected_receipts_in_window for p in member_positions)
    demand = sum(p.projected_demand for p in member_positions)
    net = avail + receipts - demand
    first = member_positions[0]
    return NetPosition(
        pn=first.pn,
        location=first.location,
        group_id=first.group_id,
        window_days=first.window_days,
        available=avail,
        expected_receipts_in_window=receipts,
        projected_demand=demand,
        net=net,
        shortage=max(0.0, -net),
    )


def apportion(
    values: tuple[int, int, int, int], *, members: list[str], trailing_consumption: dict[str, float]
) -> dict[str, tuple[int, int, int, int]]:
    """Apportion group-level (rop, eoq, ss, max) to members proportional to trailing
    12-month consumption (spec §7.6). Shares always sum to 1 (numerator/denominator use the
    same effective-consumption map, so apportioned levels never exceed the group totals)."""
    eff = {m: trailing_consumption.get(m, 0.0) for m in members}
    total = sum(eff.values())
    if total <= 0:  # no consumption signal — split evenly
        eff = {m: 1.0 for m in members}
        total = float(len(members))
    out: dict[str, tuple[int, int, int, int]] = {}
    for m in members:
        share = eff[m] / total
        out[m] = tuple(int(round(v * share)) for v in values)  # type: ignore[assignment]
    return out
