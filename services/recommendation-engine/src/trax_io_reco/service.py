"""RecommendationService — the deterministic orchestration (spec §4.1).

assemble → classify regime → project demand → policy → 4 recommenders → arbitrate →
AOG score → tier + confidence → rank → RecommendationBatch.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from trax_io_feature_store import FeatureStoreClient, FeatureStoreLookupError, TenantContext

from trax_io_reco.arbitration import arbitrate
from trax_io_reco.candidate.service import (
    ServedCandidateMember,
    build_service_frontier,
)
from trax_io_reco.confidence import confidence_score
from trax_io_reco.contracts.candidate import CandidatePreviewBatch
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
from trax_io_reco.demand.basis import demand_basis_trace
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector
from trax_io_reco.policy.mini_engine import MiniPolicyEngine, PolicyConstraintViolation
from trax_io_reco.position.net_position import (
    available,
    net_position,
    rollup_net,
    two_way_members,
)
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
        projector: DemandProjectorProtocol | None = None,
    ) -> None:
        self._config = config or TenantPolicyConfig()
        self._fr = FeatureReader(feature_store)
        self._inv = inventory_state
        self._assembler = ContextAssembler(
            features=self._fr, inventory_state=self._inv, config=self._config
        )
        self._projector = projector or HistoricalScheduledProjector()
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
        as_of: date | None = None,
        reporting_horizon_days: int = 30,
    ) -> RecommendationBatch:
        """Run the backward-compatible recommendation-only service contract."""

        return self._run(
            tenant=tenant,
            keys=keys,
            now=now,
            as_of=as_of,
            reporting_horizon_days=reporting_horizon_days,
            include_frontiers=False,
        ).recommendation_batch

    def run_with_frontiers(
        self,
        *,
        tenant: TenantContext,
        keys: list[tuple[str, str]],
        now: datetime,
        as_of: date | None = None,
        reporting_horizon_days: int = 30,
    ) -> CandidatePreviewBatch:
        """Run recommendations plus deterministic, budget-independent key frontiers."""

        return self._run(
            tenant=tenant,
            keys=keys,
            now=now,
            as_of=as_of,
            reporting_horizon_days=reporting_horizon_days,
            include_frontiers=True,
        )

    def _run(
        self,
        *,
        tenant: TenantContext,
        keys: list[tuple[str, str]],
        now: datetime,
        as_of: date | None,
        reporting_horizon_days: int,
        include_frontiers: bool,
    ) -> CandidatePreviewBatch:
        planning_as_of = as_of or now.date()
        generated_at = now.astimezone(UTC) if now.tzinfo is not None else now
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
                skipped.append(SkippedKey(pn=pn, location=location, reason="invalid_unit_cost"))
                continue
            basis = demand_basis_trace(ctx.demand_history)
            if basis.exposure_days <= 0 or basis.observation_window_source == "unavailable":
                skipped.append(
                    SkippedKey(
                        pn=pn,
                        location=location,
                        reason="demand_history_unavailable",
                    )
                )
                continue
            events = events_24mo_from(ctx.demand_history)
            regime = classify(events_24mo=events, history_days=basis.exposure_days)
            projection = self._projector.project(context=ctx, regime=regime)
            policy = self._engine.recommend(
                context=ctx,
                regime=regime,
                projection=projection,
            )
            if isinstance(policy, PolicyConstraintViolation):
                skipped.append(
                    SkippedKey(pn=pn, location=location, reason=f"policy:{policy.reason}")
                )
                continue
            prepared[(pn, location)] = _Prepared(ctx, regime, projection, policy, events)

        donor_index = self._build_donor_index(contexts)
        group_loc_index = self._build_group_loc_index(prepared)
        all_recs: list[Recommendation] = []
        frontiers = []

        for (_pn, location), prep in prepared.items():
            ctx, regime, projection, policy, events = (
                prep.ctx,
                prep.regime,
                prep.projection,
                prep.policy,
                prep.events,
            )
            members = self._group_members(ctx, group_loc_index)
            expected_member_keys = self._expected_group_member_keys(ctx)
            served_members = members if len(expected_member_keys) > 1 else [prep]
            included_member_keys = {
                f"{member.ctx.pn}@{member.ctx.location}" for member in served_members
            }
            excluded_member_keys = tuple(sorted(expected_member_keys - included_member_keys))
            snapshot = self._hash_served_inputs(
                served_members,
                expected_member_keys=expected_member_keys,
                as_of=planning_as_of,
                reporting_horizon_days=reporting_horizon_days,
            )

            def _npf(
                w: int,
                c: PartLocationContext = ctx,
                p=projection,
                loc: str = location,
                expected=frozenset(expected_member_keys),
                served=tuple(served_members),
                excluded=excluded_member_keys,
            ):
                # Same-location interchange rollup: pool dispatchable stock + demand across
                # two-way group members present in the work-list (spec §7.6 — no over-buy).
                if len(expected) > 1:
                    return rollup_net(
                        [
                            net_position(
                                context=m.ctx,
                                projection=m.projection,
                                window_days=w,
                                as_of=planning_as_of,
                            )
                            for m in served
                        ],
                        excluded_member_keys=excluded,
                    )
                return net_position(
                    context=c,
                    projection=p,
                    window_days=w,
                    as_of=planning_as_of,
                )

            def _dlf(_pn: str, _gid, _mwh, _loc: str = location) -> list[DonorOption]:
                return [d for d in donor_index.get(_pn, []) if d.location != _loc]

            inp = RecommenderInput(
                context=ctx,
                projection=projection,
                policy=policy,
                now=generated_at,
                as_of=planning_as_of,
                input_snapshot_hash=snapshot,
                reporting_horizon_days=reporting_horizon_days,
                net_position=_npf,
                donor_lookup=_dlf,
            )

            # The group-replenishment recommenders (Purchase, Transfer) run only for the
            # representative member of a multi-member interchange group, so a pooled group
            # shortage is bought once — not once per member (no N x over-buy).
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
            conf = confidence_score(
                events_24mo=events,
                regime=regime,
                used_stub_inputs=stub_inputs,
            )
            finalized_key_recs: list[Recommendation] = []
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
                finalized = r.model_copy(update={"confidence_score": conf})
                finalized_key_recs.append(finalized)
                all_recs.append(finalized)

            if include_frontiers:
                frontiers.append(
                    build_service_frontier(
                        inp=inp,
                        served_members=tuple(
                            ServedCandidateMember(
                                context=member.ctx,
                                projection=member.projection,
                                policy=member.policy,
                            )
                            for member in served_members
                        ),
                        expected_member_keys=tuple(sorted(expected_member_keys)),
                        finalized_recommendations=tuple(finalized_key_recs),
                        confidence=Decimal(str(conf)),
                    )
                )

        ranked = rank(all_recs)
        _LOG.info(
            "batch_generated",
            extra={"tenant": tenant.tenant_id, "recs": len(ranked), "skipped": len(skipped)},
        )
        batch = RecommendationBatch(
            tenant_id=tenant.tenant_id,
            generated_at=generated_at,
            reporting_horizon_days=reporting_horizon_days,
            recommendations=tuple(ranked),
            skipped=tuple(skipped),
            summary=self._summary(ranked),
        )
        return CandidatePreviewBatch(
            tenant_id=tenant.tenant_id,
            recommendation_batch=batch,
            frontiers=tuple(
                sorted(frontiers, key=lambda frontier: frontier.decision_key)
            ),
        )

    # ---- helpers ---- #
    @staticmethod
    def _history_days(ctx: PartLocationContext) -> int:
        return demand_basis_trace(ctx.demand_history).exposure_days

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
        return sorted(
            (m for m in candidates if m.ctx.pn in two_way),
            key=lambda member: (member.ctx.pn, member.ctx.location),
        )

    @staticmethod
    def _expected_group_member_keys(ctx: PartLocationContext) -> set[str]:
        graph = ctx.interchange_group
        if graph is None:
            return {f"{ctx.pn}@{ctx.location}"}
        return {f"{pn}@{ctx.location}" for pn in two_way_members(graph)}

    def _build_donor_index(
        self, contexts: dict[tuple[str, str], PartLocationContext]
    ) -> dict[str, list[DonorOption]]:
        index: dict[str, list[DonorOption]] = {}
        for (pn, location), ctx in contexts.items():
            excess = available(ctx.stock_position) - ctx.current_policy.max_stock
            if excess > 0:
                index.setdefault(pn, []).append(
                    DonorOption(
                        location=location,
                        serviceable_excess=excess,
                        lead_days=_INTRA_NETWORK_TRANSFER_DAYS,
                        cost=0.0,
                    )
                )
        return index

    @staticmethod
    def _hash_served_inputs(
        members: list[_Prepared],
        *,
        expected_member_keys: set[str],
        as_of,
        reporting_horizon_days: int,
    ) -> str:
        """Hash every immutable input that can change a served key calculation."""
        payload = json.dumps(
            _canonicalize(
                {
                    "members": [
                        {
                            "context": member.ctx.model_dump(mode="json"),
                            "projection": member.projection.model_dump(mode="json"),
                            "policy": member.policy.model_dump(mode="json"),
                        }
                        for member in sorted(
                            members,
                            key=lambda item: (item.ctx.pn, item.ctx.location),
                        )
                    ],
                    "expected_member_keys": sorted(expected_member_keys),
                    "as_of": as_of.isoformat(),
                    "reporting_horizon_days": reporting_horizon_days,
                }
            ),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _hash(ctx: PartLocationContext) -> str:
        """Backward-compatible single-context digest used by existing callers."""
        payload = json.dumps(
            _canonicalize(ctx.model_dump(mode="json")),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _stub_inputs(ctx: PartLocationContext) -> set[str]:
        stubs: set[str] = set()
        if ctx.repair_tat.n_observations == 0:
            stubs.add("repair_tat")
        if ctx.aog_signal == AogSignal():
            stubs.add("aog")
        # An observed-empty requisition feed is evidence, not a stub.  Preserve
        # the distinction between "no dated demand was observed" and "the
        # source was unavailable" when confidence is scored.
        if ctx.scheduled_demand_status != "available":
            stubs.add("scheduled_demand")
        return stubs

    @staticmethod
    def _delta_pct(rec: Recommendation) -> float:
        if rec.policy is not None and rec.current_policy is not None:
            cur, pro = rec.current_policy, rec.policy
            pairs = [
                (pro.rop, cur.rop),
                (pro.eoq, cur.eoq),
                (pro.safety_stock, cur.safety_stock),
                (pro.max_stock, cur.max_stock),
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
