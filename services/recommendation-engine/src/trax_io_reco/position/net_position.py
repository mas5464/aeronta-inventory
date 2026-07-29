"""Net-position calculator — the shared primitive every recommender keys off (spec §5.5).

available = max(0, serviceable - allocated_reserved)   (in-repair / rental / loan excluded)
expected_receipts = dated open procurement orders due in window

Aggregate in-repair balances receive no forward-supply credit until the
identity-aware, age-conditioned repair-return model is available.
net = available + expected_receipts - projected_demand
shortage = max(0, -net)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from trax_io_feature_store.schemas import InterchangeableGraph, OpenOrdersSnapshot

from trax_io_reco.contracts.context import (
    DemandProjection,
    NetPosition,
    NetPositionMember,
    PartLocationContext,
    RepairTat,
    StockPosition,
)
from trax_io_reco.demand.basis import scheduled_units_in_horizon


@dataclass(frozen=True)
class OpenReceiptHorizon:
    """Open-order quantities visible in one inclusive horizon."""

    open_receipts_due: int
    overdue_open_receipts_due: int


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
    """Inbound quantity expected within the window.

    Phase 1 credits dated, still-open order lines only.  Aggregate in-repair stock
    is intentionally *not* treated as a future receipt: without stable repair-order
    identity and an age-conditioned return model, doing so can double count the same
    physical units and overstate supply.  The later repair-return phases add that
    typed evidence explicitly.
    """
    receipt_trace = open_receipts_in_horizon(
        open_orders,
        as_of=as_of,
        horizon_days=window_days,
    )
    # Preserve the public call signature while making the conservative exclusion
    # explicit until identity-aware repair-return evidence exists.
    del repair_tat, stock_position
    return float(receipt_trace.open_receipts_due)


def open_receipts_in_horizon(
    open_orders: OpenOrdersSnapshot | None,
    *,
    as_of: date,
    horizon_days: int,
) -> OpenReceiptHorizon:
    """Count still-open procurement receipts due by the inclusive horizon.

    There is intentionally no lower due-date bound: an overdue order that remains
    open in the source snapshot is still visible, and its quantity is separately
    disclosed as overdue so callers do not imply guaranteed supply. Repair orders
    are excluded because the identity-aware repair pipeline owns those same units;
    counting them here would overlap procurement receipts and repair WIP.
    """

    if horizon_days < 0:
        raise ValueError("horizon_days must be non-negative")
    horizon_end = as_of + timedelta(days=horizon_days)
    due = overdue = 0
    if open_orders is not None:
        for order in open_orders.orders:
            if str(order.order_type).upper() != "PO":
                continue
            expected = order.expected_rcv_date
            if expected is None or expected > horizon_end:
                continue
            due += int(order.qty_open)
            if expected < as_of:
                overdue += int(order.qty_open)
    return OpenReceiptHorizon(
        open_receipts_due=due,
        overdue_open_receipts_due=overdue,
    )


def net_position(
    *,
    context: PartLocationContext,
    projection: DemandProjection,
    window_days: int,
    as_of: date,
) -> NetPosition:
    avail = available(context.stock_position)
    receipt_trace = open_receipts_in_horizon(
        context.open_orders,
        as_of=as_of,
        horizon_days=window_days,
    )
    open_receipts = float(receipt_trace.open_receipts_due)
    # Conservative Phase-1 rule: aggregate in-repair stock receives no future
    # supply credit until repair-order identity and residual-life evidence exist.
    repair_receipts = 0.0
    receipts = open_receipts + repair_receipts
    historical = float(projection.historical_component) * window_days
    scheduled = scheduled_units_in_horizon(
        context.scheduled_demand,
        as_of=as_of,
        horizon_days=window_days,
    )
    demand = historical + scheduled
    net = avail + receipts - demand
    gid = context.interchange_group.group_id if context.interchange_group else None
    scheduled_undated = tuple(
        line
        for line in (context.requisition.lines if context.requisition is not None else ())
        if line.need_by is None
    )
    open_undated = tuple(
        order
        for order in (context.open_orders.orders if context.open_orders is not None else ())
        if str(order.order_type).upper() == "PO"
        and order.expected_rcv_date is None
    )
    open_status = (
        "unavailable"
        if context.open_orders is None
        else ("partial" if open_undated else "available")
    )
    member = NetPositionMember(
        pn=context.pn,
        location=context.location,
        projection_kind=projection.dist_kind,
        projected_historical_demand=historical,
        scheduled_demand_in_window=scheduled,
        projected_demand=demand,
        available=avail,
        open_receipts_in_window=open_receipts,
        overdue_open_receipts_in_window=float(receipt_trace.overdue_open_receipts_due),
        repair_receipts_in_window=repair_receipts,
        expected_receipts_in_window=receipts,
        net=net,
        scheduled_demand_status=context.scheduled_demand_status,
        scheduled_demand_undated_lines=len(scheduled_undated),
        scheduled_demand_undated_units=sum(int(line.qty_needed) for line in scheduled_undated),
        open_receipts_status=open_status,
        open_receipts_undated_lines=len(open_undated),
        open_receipts_undated_units=sum(int(order.qty_open) for order in open_undated),
    )
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
        projected_historical_demand=historical,
        scheduled_demand_in_window=scheduled,
        open_receipts_in_window=open_receipts,
        overdue_open_receipts_in_window=float(receipt_trace.overdue_open_receipts_due),
        repair_receipts_in_window=repair_receipts,
        member_contributions=(member,),
        scheduled_demand_status=context.scheduled_demand_status,
        open_receipts_status=open_status,
        scheduled_demand_undated_lines=member.scheduled_demand_undated_lines,
        scheduled_demand_undated_units=member.scheduled_demand_undated_units,
        open_receipts_undated_lines=member.open_receipts_undated_lines,
        open_receipts_undated_units=member.open_receipts_undated_units,
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


def _aggregate_status(values: list[str]) -> str:
    states = set(values)
    if states == {"available"}:
        return "available"
    if states == {"unavailable"}:
        return "unavailable"
    return "partial"


def rollup_net(
    member_positions: list[NetPosition],
    *,
    excluded_member_keys: tuple[str, ...] = (),
) -> NetPosition:
    """Aggregate net position across interchange-group members (spec §7.6)."""
    avail = sum(p.available for p in member_positions)
    receipts = sum(p.expected_receipts_in_window for p in member_positions)
    demand = sum(p.projected_demand for p in member_positions)
    historical = sum(p.projected_historical_demand for p in member_positions)
    scheduled = sum(p.scheduled_demand_in_window for p in member_positions)
    open_receipts = sum(p.open_receipts_in_window for p in member_positions)
    overdue_receipts = sum(p.overdue_open_receipts_in_window for p in member_positions)
    repair_receipts = sum(p.repair_receipts_in_window for p in member_positions)
    net = avail + receipts - demand
    first = member_positions[0]
    contributions: list[NetPositionMember] = []
    for position in member_positions:
        if position.member_contributions:
            contributions.extend(position.member_contributions)
            continue
        # Compatibility for direct callers that construct the pre-Phase-1
        # NetPosition shape.  New engine positions always take the exact branch
        # above; this fallback merely keeps the additive contract usable.
        contributions.append(
            NetPositionMember(
                pn=position.pn,
                location=position.location,
                projection_kind="EMPIRICAL",
                projected_historical_demand=position.projected_demand,
                projected_demand=position.projected_demand,
                available=position.available,
                open_receipts_in_window=position.expected_receipts_in_window,
                expected_receipts_in_window=position.expected_receipts_in_window,
                net=position.net,
                scheduled_demand_status=position.scheduled_demand_status,
                scheduled_demand_undated_lines=(position.scheduled_demand_undated_lines),
                scheduled_demand_undated_units=(position.scheduled_demand_undated_units),
                open_receipts_status=position.open_receipts_status,
                open_receipts_undated_lines=position.open_receipts_undated_lines,
                open_receipts_undated_units=position.open_receipts_undated_units,
            )
        )
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
        projected_historical_demand=historical,
        scheduled_demand_in_window=scheduled,
        open_receipts_in_window=open_receipts,
        overdue_open_receipts_in_window=overdue_receipts,
        repair_receipts_in_window=repair_receipts,
        member_contributions=tuple(contributions),
        scheduled_demand_status=_aggregate_status(
            [member.scheduled_demand_status for member in contributions]
        ),
        open_receipts_status=_aggregate_status(
            [member.open_receipts_status for member in contributions]
        ),
        pooling_scope=(
            "worklist_partial"
            if excluded_member_keys
            else ("complete_group" if len(contributions) > 1 else "single_key")
        ),
        excluded_member_keys=tuple(sorted(set(excluded_member_keys))),
        scheduled_demand_undated_lines=sum(
            member.scheduled_demand_undated_lines for member in contributions
        ),
        scheduled_demand_undated_units=sum(
            member.scheduled_demand_undated_units for member in contributions
        ),
        open_receipts_undated_lines=sum(
            member.open_receipts_undated_lines for member in contributions
        ),
        open_receipts_undated_units=sum(
            member.open_receipts_undated_units for member in contributions
        ),
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
