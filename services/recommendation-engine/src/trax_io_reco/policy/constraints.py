"""Hard constraints (spec §6.3). Applied after the policy math; may only TIGHTEN.
If tightening breaks a floor, returns a violation so the caller routes the key to
``skipped`` rather than constructing an invalid PolicyRecommendation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from trax_io_feature_store.schemas import PartAttributes

from trax_io_reco.contracts.context import CurrentPolicy
from trax_io_reco.contracts.policy import AppliedConstraint


@dataclass(frozen=True)
class ConstraintResult:
    values: tuple[int, int, int, int] | None
    flags: list[str] = field(default_factory=list)
    violation: str | None = None
    applied_constraints: tuple[AppliedConstraint, ...] = ()


def apply_constraints(
    values: tuple[int, int, int, int],
    *,
    part_attributes: PartAttributes,
    current_policy: CurrentPolicy,
    avg_daily_demand: float,
    min_order_qty: int,
    available_plus_receipts: float | None = None,
) -> ConstraintResult:
    rop, eoq, safety_stock, max_stock = values
    flags: list[str] = []
    applied: list[AppliedConstraint] = []

    before_eoq = eoq
    eoq = max(eoq, min_order_qty)
    applied.append(
        AppliedConstraint(
            name="minimum_order_quantity",
            value=str(min_order_qty),
            binding=eoq != before_eoq,
            source="vendor_economics.minimum_order_qty",
        )
    )

    before_rop = rop
    rop = max(rop, safety_stock)
    applied.append(
        AppliedConstraint(
            name="reorder_point_floor",
            value=str(safety_stock),
            binding=rop != before_rop or rop == safety_stock,
            source="policy.floor.rop_gte_safety_stock",
        )
    )

    max_floor = rop + eoq
    before_max_floor = max_stock
    max_stock = max(max_stock, max_floor)
    applied.append(
        AppliedConstraint(
            name="maximum_stock_floor",
            value=str(max_floor),
            binding=max_stock != before_max_floor or max_stock == max_floor,
            source="policy.floor.max_gte_rop_plus_eoq",
        )
    )

    # Shelf-life clamp: Max / avg_daily_demand <= 60% of shelf life, so the
    # maximum units are the usable-life days multiplied by units/day.
    if part_attributes.shelf_life_days:
        cap = int(math.floor(0.6 * part_attributes.shelf_life_days * max(avg_daily_demand, 0.0)))
        before_cap = max_stock
        if max_stock > cap:
            max_stock = cap
            flags.append("shelf_life_clamped")
        applied.append(
            AppliedConstraint(
                name="shelf_life_cap",
                value=str(cap),
                binding=max_stock != before_cap,
                source="part_attributes.shelf_life_days",
            )
        )

    # Hazmat / tool-control: Max cannot increase more than 2x per cycle.
    if (part_attributes.hazardous_material or part_attributes.tool_control_item) and (
        current_policy.max_stock > 0
    ):
        cap = 2 * current_policy.max_stock
        before_cap = max_stock
        if max_stock > cap:
            max_stock = cap
            flags.append("hazmat_tool_capped")
        source = (
            "part_attributes.hazardous_material+tool_control_item"
            if part_attributes.hazardous_material and part_attributes.tool_control_item
            else (
                "part_attributes.hazardous_material"
                if part_attributes.hazardous_material
                else "part_attributes.tool_control_item"
            )
        )
        applied.append(
            AppliedConstraint(
                name="hazmat_tool_cycle_cap",
                value=str(cap),
                binding=max_stock != before_cap,
                source=source,
            )
        )

    # Open-order deferral: if on-hand + receipts already exceed the proposed Max, defer.
    if available_plus_receipts is not None and available_plus_receipts > max_stock:
        flags.append("open_order_deferral")
    if available_plus_receipts is not None:
        applied.append(
            AppliedConstraint(
                name="open_order_deferral",
                value=format(available_plus_receipts, ".6g"),
                binding=available_plus_receipts > max_stock,
                source="stock_position.serviceable+open_orders_snapshot",
            )
        )

    # Re-check floors after tightening; a broken floor is a violation (route to skipped).
    if max_stock < rop + eoq or rop < safety_stock or max_stock < 0:
        return ConstraintResult(
            None,
            flags,
            f"floor_violation rop={rop} eoq={eoq} ss={safety_stock} max={max_stock}",
            tuple(applied),
        )
    return ConstraintResult(
        (rop, eoq, safety_stock, max_stock),
        flags,
        None,
        tuple(applied),
    )
