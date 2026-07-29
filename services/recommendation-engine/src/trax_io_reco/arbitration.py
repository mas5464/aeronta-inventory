"""Arbitration stage (spec §7.5). Removes contradictions among the recommendations
emitted for a single (PN, Location) before scoring. Deterministic."""

from __future__ import annotations

import math
from decimal import Decimal

from trax_io_reco.contracts.context import NetPosition
from trax_io_reco.contracts.enums import RecommendationType
from trax_io_reco.contracts.recommendation import Recommendation

_EXCESS_TYPES = (RecommendationType.REDUCE_STOCK, RecommendationType.SELL)


def arbitrate(
    recs_for_key: list[Recommendation], *, net: NetPosition, min_order_qty: int = 1
) -> list[Recommendation]:
    """Return a contradiction-free recommendation set for one key."""
    out = list(recs_for_key)

    # Rule 2: a shortage key (net < 0) cannot also be 'excess' — drop Reduce/Sell.
    if net.net < 0:
        out = [r for r in out if r.type not in _EXCESS_TYPES]

    # Rule 1: Transfer before Purchase — keep transfer, recompute purchase residual.
    transfer_qty = sum(r.recommended_quantity for r in out if r.type == RecommendationType.TRANSFER)
    if transfer_qty > 0 and any(r.type == RecommendationType.PURCHASE for r in out):
        rebuilt: list[Recommendation] = []
        for r in out:
            if r.type != RecommendationType.PURCHASE:
                rebuilt.append(r)
                continue
            residual = r.shortage_quantity - transfer_qty
            if residual <= 0:
                continue  # transfer fully covers the shortage — drop the purchase
            buy = max(min_order_qty, int(math.ceil(residual)))  # never a 'buy 0'; keep MinOQ floor
            original_quantity = Decimal(str(r.recommended_quantity))
            unit_cost = (
                r.estimated_cost_impact / original_quantity
                if original_quantity > 0
                else Decimal("0")
            )
            action_constraints = tuple(
                constraint.model_copy(
                    update={
                        "value": str(min_order_qty),
                        "binding": buy > int(math.ceil(residual)),
                    }
                )
                if constraint.name == "minimum_order_quantity_action"
                else constraint
                for constraint in r.applied_constraints
            )
            rebuilt.append(
                r.model_copy(
                    update={
                        "recommended_quantity": float(buy),
                        "shortage_quantity": residual,
                        "estimated_cost_impact": unit_cost * Decimal(buy),
                        "reason": (f"Residual after transfer of {int(transfer_qty)}: buy {buy}"),
                        "applied_constraints": action_constraints,
                    }
                )
            )
        out = rebuilt
    return out
