"""Hard constraints (spec §6.3). Applied after the policy math; may only TIGHTEN.
If tightening breaks a floor, returns a violation so the caller routes the key to
``skipped`` rather than constructing an invalid PolicyRecommendation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from trax_io_feature_store.schemas import PartAttributes

from trax_io_reco.contracts.context import CurrentPolicy


@dataclass(frozen=True)
class ConstraintResult:
    values: tuple[int, int, int, int] | None
    flags: list[str] = field(default_factory=list)
    violation: str | None = None


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

    eoq = max(eoq, min_order_qty)

    # Shelf-life clamp: Max * avg_daily_demand <= 0.6 * shelf_life_days.
    if part_attributes.shelf_life_days:
        cap = int(math.floor(0.6 * part_attributes.shelf_life_days / max(avg_daily_demand, 1e-9)))
        if max_stock > cap:
            max_stock = cap
            flags.append("shelf_life_clamped")

    # Hazmat / tool-control: Max cannot increase more than 2x per cycle.
    if (part_attributes.hazardous_material or part_attributes.tool_control_item) and (
        current_policy.max_stock > 0
    ):
        cap = 2 * current_policy.max_stock
        if max_stock > cap:
            max_stock = cap
            flags.append("hazmat_tool_capped")

    # Open-order deferral: if on-hand + receipts already exceed the proposed Max, defer.
    if available_plus_receipts is not None and available_plus_receipts > max_stock:
        flags.append("open_order_deferral")

    # Re-check floors after tightening; a broken floor is a violation (route to skipped).
    if max_stock < rop + eoq or rop < safety_stock or max_stock < 0:
        return ConstraintResult(
            None,
            flags,
            f"floor_violation rop={rop} eoq={eoq} ss={safety_stock} max={max_stock}",
        )
    return ConstraintResult((rop, eoq, safety_stock, max_stock), flags, None)
