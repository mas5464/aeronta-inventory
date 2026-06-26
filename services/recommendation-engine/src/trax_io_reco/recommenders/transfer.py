"""Transfer recommender (spec §7.3). Fires when this location is short and a sibling
location holds dispatchable excess reachable by a valid (directed) substitution, and the
transfer is at least as fast as buying."""

from __future__ import annotations

from decimal import Decimal

from trax_io_reco.contracts.enums import EvidenceKind, RecommendationType
from trax_io_reco.contracts.recommendation import Evidence, Recommendation
from trax_io_reco.policy.lead_time import protection_period_days
from trax_io_reco.recommenders.base import RecommenderInput, build_recommendation, protection_window


class TransferRecommender:
    def propose(self, inp: RecommenderInput) -> list[Recommendation]:
        window = protection_window(inp)
        np_ = inp.net_position(window)
        if np_.shortage <= 0:
            return []

        group_id = (
            inp.context.interchange_group.group_id if inp.context.interchange_group else None
        )
        main_wh = (
            inp.context.location_graph.node.related_main_warehouse
            if inp.context.location_graph
            else None
        )
        donors = [
            d
            for d in inp.donor_lookup(inp.context.pn, group_id, main_wh)
            if d.serviceable_excess > 0
        ]
        if not donors:
            return []

        purchase_lead = protection_period_days(inp.context)
        # Prefer the fastest donor; emit only when transfer is at least as fast as buying.
        donors.sort(key=lambda d: (d.lead_days, d.cost, d.location))
        donor = donors[0]
        if donor.lead_days > purchase_lead:
            return []

        qty = min(int(np_.shortage) + 1, donor.serviceable_excess)
        if qty <= 0:
            return []

        unit_cost = inp.context.vendor_economics.unit_cost
        evidence = (
            Evidence(
                kind=EvidenceKind.DONOR_STOCK,
                ref_id=donor.location,
                detail=(
                    f"{donor.location} holds {donor.serviceable_excess} excess, "
                    f"transfer lead {donor.lead_days:.0f}d vs buy {purchase_lead:.0f}d"
                ),
            ),
        )
        reason = (
            f"Transfer {qty} from {donor.location} (excess {donor.serviceable_excess}); "
            f"faster/cheaper than purchase (lead {donor.lead_days:.0f}d vs {purchase_lead:.0f}d)"
        )
        return [
            build_recommendation(
                inp,
                type=RecommendationType.TRANSFER,
                recommended_location=donor.location,
                current_stock=inp.context.stock_position.serviceable,
                projected_demand=np_.projected_demand,
                shortage_quantity=np_.shortage,
                recommended_quantity=float(qty),
                estimated_cost_impact=Decimal(qty) * unit_cost,  # avoided purchase outlay
                reason=reason,
                evidence=evidence,
                horizon_days=window,
            )
        ]
