"""RecommendationService — the deterministic orchestration (spec §4.1).

assemble → classify regime → project demand → policy → 4 recommenders → arbitrate →
AOG score → tier + confidence → rank → RecommendationBatch.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from trax_io_feature_store import FeatureStoreClient, FeatureStoreLookupError, TenantContext

from trax_io_reco.arbitration import arbitrate
from trax_io_reco.confidence import confidence_score
from trax_io_reco.contracts.context import (
    AogSignal,
    DemandProjection,
    PartLocationContext,
    TenantPolicyConfig,
)
from trax_io_reco.contracts.enums import AutonomyTier, Regime
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import (
    BatchSummary,
    Recommendation,
    RecommendationBatch,
    SkippedKey,
)
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InventoryStateLookupError, InventoryStateProvider
from trax_io_reco.demand.projection import HistoricalScheduledProjector
from trax_io_reco.policy.mini_engine import MiniPolicyEngine, PolicyConstraintViolation
from trax_io_reco.position.net_position import net_position, rollup_net, two_way_members
from trax_io_reco.ranking import rank, suggest_tier
from trax_io_reco.recommenders.adjust_min_max import AdjustMinMaxRecommender
from trax_io_reco.recommenders.base import DonorOption, RecommenderInput, protection_window
from trax_io_reco.recommenders.purchase import PurchaseRecommender
from trax_io_reco.recommenders.reduce_sell import ReduceSellRecommender
from trax_io_reco.recommenders.transfer import TransferRecommender
from trax_io_reco.regime.classifier import classify, events_24mo_from
from trax_io_reco.risk.aog import AogRiskScorer

_LOG = logging.getLogger("trax_io.reco.service")
_INTRA_NETWORK_TRANSFER_DAYS = 3.0


def _canonicalize(obj: object) -> object:
    """Recursively normalize a JSON-dumped context so numerically-equal values hash the
    same: Decimal strings lose trailing-zero scale; floats round to 9 significant places."""
    if isinstance(obj, dict):
        return {k: _canonicalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_canonicalize(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 9)
    if isinstance(obj, str):
        try:
            return format(Decimal(obj).normalize(), "f")  # "100.00" -> "100", "0.50" -> "0.5"
        except (InvalidOperation, ValueError):
            return obj
    return obj


@dataclass(frozen=True)
class _Prepared:
    ctx: PartLocationContext
    regime: Regime
    projection: DemandProjection
    policy: PolicyRecommendation
    events: int


class RecommendationService:
    def __init__(
        self,
        *,
        feature_store: FeatureStoreClient,
        inventory_state: InventoryStateProvider,
        config: TenantPolicyConfig | None = None,
    ) -> None:
        self._config = config or TenantPolicyConfig()
        self._fr = FeatureReader(feature_store)
        self._inv = inventory_state
        self._assembler = ContextAssembler(
            features=self._fr, inventory_state=self._inv, config=self._config
        )
        self._projector = HistoricalScheduledProjector()
        self._engine = MiniPolicyEngine()
        self._aog = AogRiskScorer()
        # Per-member recommenders (Adjust, ReduceSell) run for every key; group-replenishment
        # recommenders (Purchase, Transfer) run only for the group's representative member so a
        # pooled interchange shortage is replenished once, not once per member.
        self._per_member = [AdjustMinMaxRecommender(), ReduceSellRecommender()]
        self._group_replenishment = [PurchaseRecommender(), TransferRecommender()]

    def run(
        self,
        *,
        tenant: TenantContext,
        keys: list[tuple[str, str]],
        now: datetime,
        reporting_horizon_days: int = 30,
    ) -> RecommendationBatch:
        as_of = now.date()
        contexts: dict[tuple[str, str], PartLocationContext] = {}
        skipped: list[SkippedKey] = []

        for pn, location in keys:
            try:
                contexts[(pn, location)] = self._assembler.assemble(
                    tenant=tenant, pn=pn, location=location
                )
            except (FeatureStoreLookupError, InventoryStateLookupError) as exc:
                skipped.append(SkippedKey(pn=pn, location=location, reason=f"missing_input:{exc}"))

        # Precompute regime/projection/policy per key; drop policy-constraint violations.
        prepared: dict[tuple[str, str], _Prepared] = {}
        for (pn, location), ctx in contexts.items():
            if ctx.vendor_economics.unit_cost <= 0:
                skipped.append(
                    SkippedKey(pn=pn, location=location, reason="invalid_unit_cost")
                )
                continue
            events = events_24mo_from(ctx.demand_history)
            regime = classify(events_24mo=events, history_days=self._history_days(ctx))
            projection = self._projector.project(context=ctx, regime=regime)
            policy = self._engine.recommend(context=ctx, regime=regime, projection=projection)
            if isinstance(policy, PolicyConstraintViolation):
                skipped.append(
                    SkippedKey(pn=pn, location=location, reason=f"policy:{policy.reason}")
                )
                continue
            prepared[(pn, location)] = _Prepared(ctx, regime, projection, policy, events)

        donor_index = self._build_donor_index(contexts)
        group_loc_index = self._build_group_loc_index(prepared)
        all_recs: list[Recommendation] = []

        for (_pn, location), prep in prepared.items():
            ctx, regime, projection, policy, events = (
                prep.ctx, prep.regime, prep.projection, prep.policy, prep.events
            )
            snapshot = self._hash(ctx)

            def _npf(w: int, c: PartLocationContext = ctx, p=projection, loc: str = location):
                # Same-location interchange rollup: pool dispatchable stock + demand across
                # two-way group members present in the work-list (spec §7.6 — no over-buy).
                members = self._group_members(c, group_loc_index)
                if len(members) > 1:
                    return rollup_net(
                        [net_position(context=m.ctx, projection=m.projection, window_days=w,
                                      as_of=as_of) for m in members]
                    )
                return net_position(context=c, projection=p, window_days=w, as_of=as_of)

            def _dlf(_pn: str, _gid, _mwh, _loc: str = location) -> list[DonorOption]:
                return [d for d in donor_index.get(_pn, []) if d.location != _loc]

            inp = RecommenderInput(
                context=ctx, projection=projection, policy=policy, now=now, as_of=as_of,
                input_snapshot_hash=snapshot, reporting_horizon_days=reporting_horizon_days,
                net_position=_npf, donor_lookup=_dlf,
            )

            # The group-replenishment recommenders (Purchase, Transfer) run only for the
            # representative member of a multi-member interchange group, so a pooled group
            # shortage is bought once — not once per member (no N x over-buy).
            members = self._group_members(ctx, group_loc_index)
            is_representative = len(members) <= 1 or ctx.pn == min(m.ctx.pn for m in members)
            recommenders = list(self._per_member)
            if is_representative:
                recommenders += self._group_replenishment

            key_recs: list[Recommendation] = []
            for rec in recommenders:
                key_recs.extend(rec.propose(inp))

            net = _npf(protection_window(inp))
            key_recs = arbitrate(
                key_recs, net=net, min_order_qty=int(ctx.vendor_economics.minimum_order_qty)
            )

            stub_inputs = self._stub_inputs(ctx)
            for r in key_recs:
                r = self._aog.score(r, context=ctx, net=net)
                if r.suggested_autonomy_tier != AutonomyTier.ADVISOR:
                    tier = suggest_tier(
                        criticality=ctx.criticality.canonical_tier,
                        unit_cost=float(ctx.vendor_economics.unit_cost),
                        delta_pct=self._delta_pct(r),
                        active_aog=False,
                    )
                    r = r.model_copy(update={"suggested_autonomy_tier": tier})
                conf = confidence_score(
                    events_24mo=events, regime=regime, used_stub_inputs=stub_inputs
                )
                all_recs.append(r.model_copy(update={"confidence_score": conf}))

        ranked = rank(all_recs)
        _LOG.info(
            "batch_generated",
            extra={"tenant": tenant.tenant_id, "recs": len(ranked), "skipped": len(skipped)},
        )
        return RecommendationBatch(
            tenant_id=tenant.tenant_id,
            generated_at=now,
            reporting_horizon_days=reporting_horizon_days,
            recommendations=tuple(ranked),
            skipped=tuple(skipped),
            summary=self._summary(ranked),
        )

    # ---- helpers ---- #
    @staticmethod
    def _history_days(ctx: PartLocationContext) -> int:
        dates = [o.period_start for o in ctx.demand_history.observations]
        if not dates:
            return 0
        return (max(dates) - min(dates)).days + 30

    @staticmethod
    def _build_group_loc_index(
        prepared: dict[tuple[str, str], _Prepared],
    ) -> dict[tuple[str, str], list[_Prepared]]:
        index: dict[tuple[str, str], list[_Prepared]] = {}
        for prep in prepared.values():
            graph = prep.ctx.interchange_group
            if graph is not None:
                index.setdefault((graph.group_id, prep.ctx.location), []).append(prep)
        return index

    @staticmethod
    def _group_members(
        ctx: PartLocationContext, index: dict[tuple[str, str], list[_Prepared]]
    ) -> list[_Prepared]:
        graph = ctx.interchange_group
        if graph is None:
            return []
        two_way = set(two_way_members(graph))
        candidates = index.get((graph.group_id, ctx.location), [])
        return [m for m in candidates if m.ctx.pn in two_way]

    def _build_donor_index(
        self, contexts: dict[tuple[str, str], PartLocationContext]
    ) -> dict[str, list[DonorOption]]:
        index: dict[str, list[DonorOption]] = {}
        for (pn, location), ctx in contexts.items():
            excess = ctx.stock_position.serviceable - ctx.current_policy.max_stock
            if excess > 0:
                index.setdefault(pn, []).append(
                    DonorOption(
                        location=location, serviceable_excess=excess,
                        lead_days=_INTRA_NETWORK_TRANSFER_DAYS, cost=0.0,
                    )
                )
        return index

    @staticmethod
    def _hash(ctx: PartLocationContext) -> str:
        # Canonical: pure-input context (no volatile fields), sorted keys, and Decimal-scale /
        # float-repr normalized so numerically-equal inputs (e.g. "100" vs "100.00") hash the
        # same — audit-reproducible (spec §7.9).
        payload = json.dumps(_canonicalize(ctx.model_dump(mode="json")), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _stub_inputs(ctx: PartLocationContext) -> set[str]:
        stubs: set[str] = set()
        if ctx.repair_tat.n_observations == 0:
            stubs.add("repair_tat")
        if ctx.aog_signal == AogSignal():
            stubs.add("aog")
        if not ctx.scheduled_demand:
            stubs.add("scheduled_demand")
        return stubs

    @staticmethod
    def _delta_pct(rec: Recommendation) -> float:
        if rec.policy is not None and rec.current_policy is not None:
            cur, pro = rec.current_policy, rec.policy
            pairs = [
                (pro.rop, cur.rop), (pro.eoq, cur.eoq),
                (pro.safety_stock, cur.safety_stock), (pro.max_stock, cur.max_stock),
            ]
            return max(abs(n - o) / max(o, 1) for n, o in pairs)
        if rec.projected_demand > 0:
            return rec.shortage_quantity / rec.projected_demand
        return 0.0

    @staticmethod
    def _summary(recs: list[Recommendation]) -> BatchSummary:
        by_type: dict[str, int] = {}
        by_aog: dict[int, int] = {}
        for r in recs:
            by_type[r.type.value] = by_type.get(r.type.value, 0) + 1
            by_aog[int(r.aog_risk_level)] = by_aog.get(int(r.aog_risk_level), 0) + 1
        return BatchSummary(total=len(recs), by_type=by_type, by_aog=by_aog)
