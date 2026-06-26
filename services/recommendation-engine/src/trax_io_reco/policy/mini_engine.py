"""Deterministic policy engine — the Adjust Min/Max anchor (spec §6.2).

Regime dispatch: ULTRA_RARE → base-stock (all tiers); INTERMITTENT → (s,S); else → (R,Q).
Applies the §6.3 constraints; a constraint violation returns PolicyConstraintViolation
so the caller routes the key to ``skipped`` (no invalid PolicyRecommendation is built).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from trax_io_reco.contracts.context import DemandProjection, PartLocationContext
from trax_io_reco.contracts.enums import PolicyKind, Regime
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.policy.base_stock import compute_base_stock
from trax_io_reco.policy.constraints import ConstraintResult, apply_constraints
from trax_io_reco.policy.lead_time import lead_mean_var
from trax_io_reco.policy.R_Q import compute_R_Q
from trax_io_reco.policy.s_S import compute_s_S


@dataclass(frozen=True)
class PolicyConstraintViolation:
    reason: str


class MiniPolicyEngine:
    def recommend(
        self, *, context: PartLocationContext, regime: Regime, projection: DemandProjection
    ) -> PolicyRecommendation | PolicyConstraintViolation:
        cfg = context.tenant_policy_config
        tier = int(context.criticality.canonical_tier)
        target = cfg.service_level_by_tier.get(tier, 0.95)
        lead_mean, lead_var = lead_mean_var(context)
        unit_cost = float(context.vendor_economics.unit_cost)
        min_oq = int(context.vendor_economics.minimum_order_qty)

        if regime == Regime.ULTRA_RARE:
            values = compute_base_stock(
                projection=projection, lead_mean=lead_mean, lead_var=lead_var,
                service_level=target,
            )
            kind = PolicyKind.BASE_STOCK
        elif regime == Regime.INTERMITTENT:
            values = compute_s_S(
                projection=projection, lead_mean=lead_mean, lead_var=lead_var,
                service_level=target, ordering_cost=cfg.ordering_cost,
                holding_cost_rate=cfg.holding_cost_rate, unit_cost=unit_cost, min_order_qty=min_oq,
            )
            kind = PolicyKind.S_S
        else:
            values = compute_R_Q(
                projection=projection, lead_mean=lead_mean, lead_var=lead_var,
                service_level=target, ordering_cost=cfg.ordering_cost,
                holding_cost_rate=cfg.holding_cost_rate, unit_cost=unit_cost, min_order_qty=min_oq,
            )
            kind = PolicyKind.R_Q

        result: ConstraintResult = apply_constraints(
            values,
            part_attributes=context.part_attributes,
            current_policy=context.current_policy,
            avg_daily_demand=projection.mean_per_day,
            min_order_qty=min_oq,
        )
        if result.violation is not None or result.values is None:
            return PolicyConstraintViolation(reason=result.violation or "no_policy")

        rop, eoq, safety_stock, max_stock = result.values
        # Deterministic, content-addressed provenance id (audit-reproducible): identical
        # inputs -> identical id. Not a random ULID (that would break determinism + audit).
        provenance_id = hashlib.sha256(
            f"{context.tenant_id}|{context.pn}|{context.location}|{kind.value}|"
            f"{rop},{eoq},{safety_stock},{max_stock}|{target}|"
            f"{projection.dist_kind}|{sorted(projection.dist_params.items())}|"
            f"{lead_mean},{lead_var}".encode()
        ).hexdigest()[:26]
        return PolicyRecommendation(
            tenant_id=context.tenant_id,
            pn=context.pn,
            location=context.location,
            rop=rop,
            eoq=eoq,
            safety_stock=safety_stock,
            max_stock=max_stock,
            policy_kind=kind,
            service_level_target=target,
            provenance_id=provenance_id,
            model_id="deterministic-v1",
        )
