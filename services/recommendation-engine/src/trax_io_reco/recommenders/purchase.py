"""Purchase recommender (spec §7.2). Fires when net position over the protection
period is negative and open orders don't already cover it."""

from __future__ import annotations

import math
from decimal import Decimal

from trax_io_reco.contracts.enums import EvidenceKind, RecommendationType
from trax_io_reco.contracts.recommendation import Evidence, Recommendation
from trax_io_reco.recommenders.base import RecommenderInput, build_recommendation, protection_window


class PurchaseRecommender:
    def propose(self, inp: RecommenderInput) -> list[Recommendation]:
        window = protection_window(inp)
        np_ = inp.net_position(window)
        if np_.net >= 0:
            return []  # covered (incl. scenario 6: open PO covers future demand)

        safety_stock = inp.policy.safety_stock
        min_oq = int(inp.context.vendor_economics.minimum_order_qty)
        buy_qty = max(min_oq, int(math.ceil(np_.shortage + safety_stock)))
        unit_cost = inp.context.vendor_economics.unit_cost

        evidence = (
            Evidence(
                kind=EvidenceKind.DEMAND_HISTORY,
                ref_id=f"{inp.context.pn}@{inp.context.location}",
                detail=f"projected demand {np_.projected_demand:.1f} over {window}d protection",
            ),
            Evidence(
                kind=EvidenceKind.OPEN_ORDER,
                ref_id=(
                    f"coverage={np_.open_receipts_status}"
                    if np_.open_receipts_status != "available"
                    else f"receipts={np_.expected_receipts_in_window:.0f}"
                ),
                detail=(
                    (
                        "Open-order coverage is "
                        f"{np_.open_receipts_status}; only dated receipts present in "
                        "the available snapshot were credited, and zero is not "
                        "presented as an observed absence. "
                    )
                    if np_.open_receipts_status != "available"
                    else ""
                )
                + (
                    f"available {np_.available:.0f} + receipts "
                    f"{np_.expected_receipts_in_window:.0f} < demand {np_.projected_demand:.1f}"
                ),
            ),
        )
        reason = (
            f"Shortage {np_.shortage:.1f} over {window}d protection period; "
            f"buy {buy_qty} (incl. safety {safety_stock})"
        )
        return [
            build_recommendation(
                inp,
                type=RecommendationType.PURCHASE,
                current_stock=inp.context.stock_position.serviceable,
                projected_demand=np_.projected_demand,
                shortage_quantity=np_.shortage,
                recommended_quantity=float(buy_qty),
                estimated_cost_impact=Decimal(buy_qty) * unit_cost,
                reason=reason,
                evidence=evidence,
                horizon_days=window,
                calculation_net=np_,
            )
        ]
