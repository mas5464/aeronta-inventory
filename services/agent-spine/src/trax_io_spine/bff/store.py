"""In-memory Planner store: the approval queue + lifecycle over the real Supervisor pipeline."""

from __future__ import annotations

import calendar
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_feature_store.snapshot import load_store
from trax_io_forecasting.projector import StatisticalProjector
from trax_io_reco.contracts.context import TenantPolicyConfig
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType
from trax_io_reco.contracts.recommendation import Recommendation
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.regime.classifier import classify, events_24mo_from
from trax_io_reco.service import RecommendationService

from trax_io_spine.bff.feeds import FEED_DEFINITIONS
from trax_io_spine.bff.models import (
    AccuracyPoint,
    ActionResult,
    Breakdown,
    BulkApproveFilter,
    DashboardSummary,
    DemandPoint,
    DemandSummary,
    FeedConnectionStatus,
    FeedHealthRow,
    FeedHealthStrip,
    FeedsSummary,
    ForecastAccuracy,
    ForecastSummary,
    FrontierPointWire,
    LeadTimeView,
    MethodCoverage,
    MethodCoverageRow,
    OpenOrderView,
    PartAttributesView,
    PartContext,
    PartShortfall,
    QueueRow,
    QueueSortKey,
    RecommendationDetail,
    RejectReason,
    Scenario,
    ScenarioAuditEvent,
    ScenarioOutcomeWire,
    ScenarioParamsWire,
    ScenarioSolveResult,
    ScenarioStatus,
    ServiceLevelBand,
    ServiceLevelPolicy,
    StockBreakdown,
    TaskStatus,
    _EvidenceView,
    _PolicyView,
)
from trax_io_spine.bff.scenario import (
    KeyStats,
    ScenarioParams,
    ScenarioSolver,
    SolveResult,
    build_key_stats,
)
from trax_io_spine.bvr.models import BvrReport
from trax_io_spine.bvr.report import KeyFacts, RecState, build_bvr_report
from trax_io_spine.contracts import (
    GuardrailOutcome,
    GuardrailStatus,
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
)
from trax_io_spine.guardrail.enforce import GuardrailEnforcer
from trax_io_spine.supervisor import to_writeback_request
from trax_io_spine.writeback.target import InMemoryWritebackTarget

# Regime -> forecast-method label (PRD §6.6 "Forecast-method coverage"). The
# deterministic Regime classifier (spec §6.1) IS the real regime assignment the engine
# runs per key; this maps each regime to the projector it is actually served by in v1
# (services/forecasting + services/recommendation-engine/src/trax_io_reco/demand):
# ultra_rare -> Empirical-Bayes (Gamma-Poisson, slice C), intermittent -> Croston/SBA/TSB
# (StatisticalProjector, slice A), moderate/high_volume -> the deterministic
# historical+scheduled projector (gradient-boosted challenger not yet in the serving
# path — see services/forecasting slice B docstring).
_REGIME_METHOD = {
    "ultra_rare": "Empirical Bayes (Gamma-Poisson)",
    "intermittent": "Croston/SBA/TSB",
    "moderate": "Historical + scheduled (moving average)",
    "high_volume": "Historical + scheduled (moving average)",
}


class KillSwitchEngaged(Exception):  # noqa: N818
    """Raised when an approve/bulk-approve is attempted while the kill switch is engaged."""


class RecommendationNotFound(Exception):  # noqa: N818
    """Raised when a recommendation_id is unknown to this tenant's store."""


class ScenarioNotFound(Exception):  # noqa: N818
    """Raised when a scenario_id is unknown to this tenant's store."""


@dataclass
class _ScenarioEntry:
    scenario: Scenario


@dataclass
class _Entry:
    rec: Recommendation
    outcome: GuardrailOutcome
    status: TaskStatus
    reject_reason: str | None = None
    reject_detail: str = ""
    deferred_until: datetime | None = None


def _policy_view(p) -> _PolicyView | None:
    if p is None:
        return None
    return _PolicyView(rop=p.rop, eoq=p.eoq, safety_stock=p.safety_stock, max_stock=p.max_stock)


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - feature groups may be absent; degrade to None
        return None


def _read_manifest(extract_dir: str) -> dict:
    """Tolerant manifest read — mirrors `extract_loader.build_stores_from_extract`'s own
    manifest handling exactly (missing file -> `{}`, corrupt JSON -> `{}` + log, never
    raises). `build_stores_from_extract` already parses this file internally but
    discards it once it has `tenant_id`/`extract_date`; Slice S7 needs the per-domain
    `artifacts` list too, so the store re-reads it once at seed time rather than
    threading a new return value through the loader's public contract."""
    path = Path(extract_dir) / "manifest.json"
    if not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


@dataclass
class PlannerStore:
    tenant_id: str
    writeback: InMemoryWritebackTarget = field(default_factory=InMemoryWritebackTarget)
    kill_switch: bool = False
    _entries: dict[str, _Entry] = field(default_factory=dict)
    fs: object | None = None
    tenant: object | None = None
    keys: list = field(default_factory=list)
    # Slice S6 — What-If Scenarios: lazily-built, memoized per-key demand/lead-time/
    # cost primitives (built once from `fs`/`keys`, reused across every solve — see
    # `bff/scenario.py` module docstring) + the in-memory saved-scenario repo.
    _key_stats_cache: list[KeyStats] | None = field(default=None, repr=False)
    _scenarios: dict[str, _ScenarioEntry] = field(default_factory=dict)
    _audit_log: list[ScenarioAuditEvent] = field(default_factory=list)
    # Slice S8 — BVR: memoized Business Value Report, invalidated by every decision
    # action (approve/reject/defer/bulk_approve/rollback) so it always reflects the
    # current lifecycle state. See `bvr()` below.
    _bvr_cache: BvrReport | None = field(default=None, repr=False)
    # Slice S7 — Data & Connections: the seeded extract's manifest, retained verbatim
    # (empty dict when the extract dir has no manifest.json, or it's corrupt/unreadable
    # — degrade gracefully rather than fail store construction over an optional file).
    # Additive-only field with a byte-compatible default; `from_extract`/`from_snapshot`
    # keep working unchanged for every existing caller that doesn't care about feeds.
    _manifest: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_extract(
        cls, *, tenant_id: str, extract_dir: str, now: datetime,
        writeback: InMemoryWritebackTarget | None = None,
        pool_by_part: bool = False,
        use_statistical: bool = False,
    ) -> PlannerStore:
        # pool_by_part: network-pooled on-hand/demand for real eMRO extracts (where
        # policies key at planning locations but stock lives at physical ones). Off by
        # default so the committed sample loads per-location exactly as before.
        # use_statistical: inject #5's StatisticalProjector (Croston/SBA/TSB) for the
        # intermittent regime instead of the deterministic HistoricalScheduledProjector
        # default. Off by default so existing behavior/tests are unchanged.
        fs, inv, tid, keys = build_stores_from_extract(
            extract_dir, tenant_id=tenant_id, pool_by_part=pool_by_part
        )
        tenant = TenantContext(tenant_id=tid)
        projector = StatisticalProjector() if use_statistical else None
        batch = RecommendationService(
            feature_store=fs, inventory_state=inv, projector=projector
        ).run(tenant=tenant, keys=keys, now=now)
        return cls._build(
            fs=fs, tenant=tenant, keys=keys,
            recommendations=batch.recommendations, writeback=writeback,
            manifest=_read_manifest(extract_dir),
        )

    @classmethod
    def from_snapshot(
        cls, *, tenant_id: str, extract_dir: str, recs_file: str, now: datetime,
        writeback: InMemoryWritebackTarget | None = None,
        pool_by_part: bool = False,
    ) -> PlannerStore:
        """Fast boot path: rebuild the feature/inventory stores from the extract (cheap —
        JSON parsing, no `RecommendationService.run`) and load precomputed recommendations
        from `recs_file` (written by `bff/precompute.py`) instead of recomputing them.

        `now` is accepted for interface symmetry with `from_extract` (the recommendations
        were already generated against a fixed `now` at precompute time) but is otherwise
        unused here — the recs are loaded as-is.
        """
        del now  # recommendations already carry their own generated_at from precompute
        fs, inv, tid, keys = build_stores_from_extract(
            extract_dir, tenant_id=tenant_id, pool_by_part=pool_by_part
        )
        del inv  # inventory_state is only needed to run the engine, not to serve a snapshot
        tenant = TenantContext(tenant_id=tid)
        raw = json.loads(Path(recs_file).read_text())
        recommendations = [Recommendation.model_validate(obj) for obj in raw]
        return cls._build(
            fs=fs, tenant=tenant, keys=keys,
            recommendations=recommendations, writeback=writeback,
            manifest=_read_manifest(extract_dir),
        )

    @classmethod
    def from_snapshot_dir(
        cls, *, tenant_id: str, snapshot_dir: str,
        writeback: InMemoryWritebackTarget | None = None,
    ) -> PlannerStore:
        """Fastest boot path: load the COMPLETE snapshot dir written by
        `bff/precompute.py` — the built (pooled) feature store, keys universe,
        manifest, and recommendations. No extract parsing, no pooling, and no
        engine run at boot; the extract dir is not needed at all (spec:
        docs/superpowers/specs/2026-07-02-fast-boot-feature-store-snapshot-design.md).

        Fail-loud by design: a missing artifact, a tenant mismatch, or feature-model
        schema drift (snapshot written by an older package version) raises rather
        than silently falling back to the slow path — unset PLANNER_SNAPSHOT_DIR to
        boot the old way.
        """
        sd = Path(snapshot_dir)
        for artifact in ("meta.json", "feature_store.json", "keys.json", "recs.json"):
            if not (sd / artifact).exists():
                raise FileNotFoundError(f"snapshot dir {snapshot_dir} is missing {artifact}")
        meta = json.loads((sd / "meta.json").read_text())
        if meta.get("tenant") != tenant_id:
            raise ValueError(
                f"snapshot tenant {meta.get('tenant')!r} does not match "
                f"requested tenant {tenant_id!r}"
            )
        if meta.get("snapshot_format") != 1:
            raise ValueError(
                f"unsupported snapshot_format {meta.get('snapshot_format')!r} "
                f"in {snapshot_dir}/meta.json (expected 1)"
            )
        fs = load_store(sd / "feature_store.json")
        keys = [tuple(k) for k in json.loads((sd / "keys.json").read_text())]
        recommendations = [
            Recommendation.model_validate(obj)
            for obj in json.loads((sd / "recs.json").read_text())
        ]
        return cls._build(
            fs=fs, tenant=TenantContext(tenant_id=tenant_id), keys=keys,
            recommendations=recommendations, writeback=writeback,
            manifest=_read_manifest(str(sd)),  # tolerant: absent manifest -> {} (feeds degrade)
        )

    @classmethod
    def _build(
        cls, *, fs, tenant: TenantContext, keys: list[tuple[str, str]],
        recommendations, writeback: InMemoryWritebackTarget | None,
        manifest: dict | None = None,
    ) -> PlannerStore:
        store = cls(tenant_id=tenant.tenant_id, writeback=writeback or InMemoryWritebackTarget())
        store.fs = fs
        store.tenant = tenant
        store.keys = list(keys)
        store._manifest = manifest or {}
        enforcer = GuardrailEnforcer()
        for rec in recommendations:
            store._ingest(rec, enforcer.enforce(rec))
        return store

    def _ingest(self, rec: Recommendation, outcome: GuardrailOutcome) -> None:
        if outcome.status is GuardrailStatus.QUEUED_FOR_APPROVAL:
            self._entries[rec.recommendation_id] = _Entry(rec, outcome, TaskStatus.PENDING)
        elif outcome.status is GuardrailStatus.APPROVED_FOR_WRITE:
            self.writeback.write(self._req(rec, outcome))
            self._entries[rec.recommendation_id] = _Entry(rec, outcome, TaskStatus.APPROVED)
        else:  # REJECTED_HARD_GUARDRAIL
            self._entries[rec.recommendation_id] = _Entry(rec, outcome, TaskStatus.REJECTED)

    def _req(self, rec: Recommendation, outcome: GuardrailOutcome):
        idem = f"{rec.tenant_id}:{rec.part_number}:{rec.current_location}:{rec.input_snapshot_hash}"
        return to_writeback_request(rec, idempotency_key=idem, tier=outcome.tier)

    def _get(self, rec_id: str) -> _Entry:
        entry = self._entries.get(rec_id)
        if entry is None:
            raise RecommendationNotFound(rec_id)
        return entry

    @staticmethod
    def _priority(entry: _Entry) -> float:
        return entry.outcome.approval_task.priority_score if entry.outcome.approval_task else 0.0

    def _row(self, entry: _Entry) -> QueueRow:
        rec = entry.rec
        return QueueRow(
            recommendation_id=rec.recommendation_id, pn=rec.part_number,
            location=rec.current_location, type=rec.type, criticality_tier=rec.criticality_tier,
            aog_risk_level=rec.aog_risk_level, confidence_score=rec.confidence_score,
            recommended_quantity=rec.recommended_quantity,
            estimated_cost_impact=rec.estimated_cost_impact, tier=entry.outcome.tier,
            priority_score=self._priority(entry), status=entry.status,
            reason=" | ".join(entry.outcome.reasons) or rec.reason,
            approvable=rec.policy is not None,
            description=rec.description,
            current_stock=rec.current_stock,
            shortage_quantity=rec.shortage_quantity,
            recommended_location=rec.recommended_location,
            horizon_days=rec.horizon_days,
        )

    def set_kill_switch(self, engaged: bool) -> None:
        self.kill_switch = engaged
        self._bvr_cache = None

    def approve(self, rec_id: str) -> ActionResult:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
        self._bvr_cache = None
        entry = self._get(rec_id)
        if entry.rec.policy is None:
            raise ValueError(f"recommendation {rec_id} has no writable policy")
        result = self.writeback.write(self._req(entry.rec, entry.outcome))
        entry.status = TaskStatus.APPROVED
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.APPROVED, writeback=result,
            message=f"written ({result.status.value})",
        )

    def reject(self, rec_id: str, reason: RejectReason, detail: str = "") -> ActionResult:
        self._bvr_cache = None
        entry = self._get(rec_id)
        entry.status = TaskStatus.REJECTED
        entry.reject_reason = reason.value
        entry.reject_detail = detail
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.REJECTED, message=reason.value
        )

    def defer(self, rec_id: str, until: datetime | None = None) -> ActionResult:
        self._bvr_cache = None
        entry = self._get(rec_id)
        entry.status = TaskStatus.DEFERRED
        entry.deferred_until = until
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.DEFERRED, message="deferred"
        )

    def _matches(self, entry: _Entry, f: BulkApproveFilter) -> bool:
        if f.tiers is not None and entry.outcome.tier not in f.tiers:
            return False
        if f.max_delta_pct is not None and entry.outcome.delta_pct > f.max_delta_pct:
            return False
        if f.criticality_min is not None and entry.rec.criticality_tier < f.criticality_min:
            return False
        return f.types is None or entry.rec.type in f.types

    def bulk_approve(self, filter: BulkApproveFilter) -> tuple[int, list[ActionResult]]:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
        self._bvr_cache = None
        targets = [
            rid for rid, e in self._entries.items()
            if e.status is TaskStatus.PENDING
            and e.rec.policy is not None
            and self._matches(e, filter)
        ]
        results = [self.approve(rid) for rid in targets]
        return len(results), results

    def history(self, *, pn: str, location: str) -> tuple[HistoryEntry, ...]:
        return self.writeback.get_history(tenant_id=self.tenant_id, pn=pn, location=location)

    def rollback(self, req: RollbackRequest) -> RollbackResult:
        self._bvr_cache = None
        return self.writeback.rollback(req)

    # Sort-key extractors for `_sorted_entries` — one per `QueueSortKey` member.
    # `estimated_cost_impact` is a `Decimal` on the underlying `Recommendation`; cast to
    # `float` so it sorts against the other (already-float) keys with the same semantics
    # a numeric `sort()` key expects.
    _SORT_KEY_FNS = {
        QueueSortKey.PRIORITY: lambda self, e: self._priority(e),
        QueueSortKey.COST_IMPACT: lambda self, e: float(e.rec.estimated_cost_impact),
        QueueSortKey.CONFIDENCE: lambda self, e: e.rec.confidence_score,
        QueueSortKey.CRITICALITY: lambda self, e: e.rec.criticality_tier,
    }

    def _sorted_entries(
        self,
        *,
        status: TaskStatus,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY,
        sort_dir: str = "desc",
        tier: AutonomyTier | None = None,
        type_: RecommendationType | None = None,
        aog_min: AogRiskLevel | None = None,
    ) -> list[_Entry]:
        # Filter first, then a stable two-pass sort: recommendation_id ASC is ALWAYS
        # applied first (as the tie-break), then the requested sort key on top of it —
        # so paging stays deterministic across requests even when many entries share
        # the same sort-key value (defaults reproduce the original priority-desc,
        # id-tie-break ordering exactly).
        entries = [e for e in self._entries.values() if e.status is status]
        if tier is not None:
            entries = [e for e in entries if e.outcome.tier == tier]
        if type_ is not None:
            entries = [e for e in entries if e.rec.type == type_]
        if aog_min is not None:
            entries = [e for e in entries if e.rec.aog_risk_level >= aog_min]

        entries.sort(key=lambda e: e.rec.recommendation_id)
        key_fn = self._SORT_KEY_FNS[sort_by]
        entries.sort(key=lambda e: key_fn(self, e), reverse=(sort_dir == "desc"))
        return entries

    def queue(self, *, status: TaskStatus = TaskStatus.PENDING, limit: int = 50) -> list[QueueRow]:
        entries = self._sorted_entries(status=status)
        return [self._row(e) for e in entries[:limit]]

    def list_queue_page(
        self,
        *,
        status: TaskStatus = TaskStatus.PENDING,
        limit: int = 50,
        offset: int = 0,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY,
        sort_dir: str = "desc",
        tier: AutonomyTier | None = None,
        type_: RecommendationType | None = None,
        aog_min: AogRiskLevel | None = None,
    ) -> tuple[list[QueueRow], int]:
        """Paged queue query: full filtered+sorted set, sliced to one page + its total.

        Free-text search intentionally stays client-side over the loaded page for now
        — not implemented server-side in this task. Sort (`sort_by`/`sort_dir`) and
        filter (`tier`/`type_`/`aog_min`) are server-side (task F2); every new keyword
        defaults to reproducing the pre-F2 behavior byte-for-byte.
        """
        entries = self._sorted_entries(
            status=status,
            sort_by=sort_by,
            sort_dir=sort_dir,
            tier=tier,
            type_=type_,
            aog_min=aog_min,
        )
        page = entries[offset : offset + limit]
        return [self._row(e) for e in page], len(entries)

    def detail(self, rec_id: str) -> RecommendationDetail:
        entry = self._get(rec_id)
        rec = entry.rec
        return RecommendationDetail(
            recommendation_id=rec.recommendation_id, pn=rec.part_number,
            location=rec.current_location, type=rec.type, criticality_tier=rec.criticality_tier,
            aog_risk_level=rec.aog_risk_level, confidence_score=rec.confidence_score,
            recommended_quantity=rec.recommended_quantity,
            estimated_cost_impact=rec.estimated_cost_impact, tier=entry.outcome.tier,
            status=entry.status, reason=" | ".join(entry.outcome.reasons) or rec.reason,
            provenance_id=rec.policy.provenance_id if rec.policy else None,
            projected_demand=rec.projected_demand,
            current_policy=_policy_view(rec.current_policy),
            proposed_policy=_policy_view(rec.policy),
            supporting_evidence=tuple(
                _EvidenceView(
                    kind=str(e.kind), ref_id=e.ref_id, detail=e.detail,
                    as_of=e.as_of.isoformat() if e.as_of else None,
                )
                for e in rec.supporting_evidence
            ),
            guardrail_flags=rec.guardrail_flags,
            description=rec.description,
            current_stock=rec.current_stock,
            shortage_quantity=rec.shortage_quantity,
            recommended_location=rec.recommended_location,
            horizon_days=rec.horizon_days,
        )

    def part_context(self, pn: str, location: str) -> PartContext:
        if (pn, location) not in self.keys:
            raise RecommendationNotFound(f"{pn}/{location}")
        t = self.tenant
        attrs = _safe(lambda: self.fs.get_part_attributes(tenant=t, pn=pn))
        crit = _safe(lambda: self.fs.get_criticality(tenant=t, pn=pn))
        sp = _safe(lambda: self.fs.get_stock_position(tenant=t, pn=pn, location=location))
        cp = _safe(lambda: self.fs.get_current_policy(tenant=t, pn=pn, location=location))
        lt = _safe(
            lambda: self.fs.get_lead_time_distribution(
                tenant=t, pn=pn, vendor="DEFAULT", condition="NEW"
            )
        )
        oo = _safe(lambda: self.fs.get_open_orders_snapshot(tenant=t, pn=pn, location=location))
        dh = _safe(lambda: self.fs.get_demand_history(tenant=t, pn=pn, location=location))
        ve = _safe(lambda: self.fs.get_vendor_economics(tenant=t, pn=pn, vendor="DEFAULT"))
        entry = next(
            (
                e
                for e in self._entries.values()
                if e.rec.part_number == pn and e.rec.current_location == location
            ),
            None,
        )
        return PartContext(
            pn=pn,
            location=location,
            attributes=PartAttributesView(
                description=(attrs.description if attrs and attrs.description else pn),
                ata_chapter=attrs.ata_chapter if attrs else None,
                part_class=attrs.part_class if attrs else None,
                shelf_life_days=attrs.shelf_life_days if attrs else None,
                hazardous_material=bool(attrs and attrs.hazardous_material),
                tool_control_item=bool(attrs and attrs.tool_control_item),
                criticality_tier=crit.canonical_tier if crit else None,
            ),
            stock=(
                StockBreakdown(
                    on_hand=sp.on_hand,
                    serviceable=sp.serviceable,
                    in_repair=sp.unserviceable_in_repair,
                    allocated=sp.allocated_reserved,
                    rental=sp.rental,
                    loan=sp.loan,
                )
                if sp
                else None
            ),
            current_policy=_policy_view(cp) if cp else None,
            proposed_policy=_policy_view(entry.rec.policy) if entry and entry.rec.policy else None,
            lead_time=(
                LeadTimeView(
                    promised_days=lt.promised_lead_days,
                    realized_mean_days=lt.realized_mean_days,
                    n_observations=lt.n_observations,
                )
                if lt
                else None
            ),
            open_orders=tuple(
                OpenOrderView(
                    order_id=o.order_id,
                    order_type=o.order_type,
                    vendor=o.vendor,
                    qty_open=o.qty_open,
                    expected_rcv_date=(
                        o.expected_rcv_date.isoformat() if o.expected_rcv_date else None
                    ),
                )
                for o in (oo.orders if oo else [])
            ),
            total_open_qty=oo.total_open_qty if oo else 0,
            demand=(
                DemandSummary(
                    total_24mo=sum(o.removals + o.issues for o in dh.observations),
                    points=tuple(
                        DemandPoint(
                            period_start=o.period_start.isoformat(),
                            removals=o.removals,
                            issues=o.issues,
                            total=o.removals + o.issues,
                        )
                        for o in sorted(dh.observations, key=lambda o: o.period_start)
                    ),
                )
                if dh
                else None
            ),
            unit_cost=float(ve.unit_cost) if ve else None,
        )

    def bvr(self) -> BvrReport:
        """The Business Value Report (spec 2026-07-02) — memoized; every decision
        action invalidates the cache so the report always reflects the current
        lifecycle state. Projected-only: see trax_io_spine.bvr."""
        if self._bvr_cache is not None:
            return self._bvr_cache
        policy_of = {}
        key_facts = []
        for ks in self._key_stats():
            pol = _safe(lambda ks=ks: self.fs.get_current_policy(
                tenant=self.tenant, pn=ks.pn, location=ks.location))
            policy_of[(ks.pn, ks.location)] = pol
            key_facts.append(KeyFacts(
                pn=ks.pn, location=ks.location, criticality_tier=ks.criticality_tier,
                rop=pol.rop if pol else 0, mean_per_day=ks.mean_per_day,
                lead_mean=ks.lead_mean,
                unit_cost=ks.unit_cost if ks.unit_cost > 0 else None,
            ))
        rec_states = [
            RecState(rec=e.rec, status=e.status.value) for e in self._entries.values()
        ]

        def baseline_for(entry):
            pol = policy_of.get((entry.pn, entry.location))
            if pol is None:
                return None
            return {"rop": pol.rop, "eoq": pol.eoq,
                    "safety_stock": pol.safety_stock, "max_stock": pol.max_stock}

        self._bvr_cache = build_bvr_report(
            tenant_id=self.tenant_id,
            extract_date=self._manifest.get("extract_date"),
            generated_at=datetime.now(UTC),
            key_facts=key_facts, rec_states=rec_states,
            ledger=self.writeback.iter_history(self.tenant_id),
            baseline_for=baseline_for, kill_switch=self.kill_switch,
        )
        return self._bvr_cache

    def dashboard(self) -> DashboardSummary:
        t = self.tenant
        rows = []  # per-key facts
        # Index entries once by (pn, location) so the per-key loop below is O(1)
        # per lookup instead of an O(n) scan into self._entries — overall
        # O(keys + entries) rather than O(keys * entries). Feature-store getters
        # (self.fs.*) are already O(1) dict lookups, so those are left as-is.
        # Multiple recommendations can share a (pn, location) key (e.g. a rejected
        # duplicate); keep the first-inserted match to mirror the original
        # next(x for x in self._entries.values() if ...) scan order exactly.
        by_key: dict[tuple[str, str], _Entry] = {}
        for e in self._entries.values():
            key = (e.rec.part_number, e.rec.current_location)
            if key not in by_key:
                by_key[key] = e
        for pn, loc in self.keys:
            sp = _safe(
                lambda pn=pn, loc=loc: self.fs.get_stock_position(tenant=t, pn=pn, location=loc)
            )
            attrs = _safe(lambda pn=pn: self.fs.get_part_attributes(tenant=t, pn=pn))
            crit = _safe(lambda pn=pn: self.fs.get_criticality(tenant=t, pn=pn))
            ve = _safe(
                lambda pn=pn: self.fs.get_vendor_economics(tenant=t, pn=pn, vendor="DEFAULT")
            )
            e = by_key.get((pn, loc))
            rec = e.rec if e else None
            rows.append(
                dict(
                    pn=pn,
                    loc=loc,
                    on_hand=sp.on_hand if sp else 0,
                    unit_cost=float(ve.unit_cost) if ve else 0.0,
                    shortage=rec.shortage_quantity if rec else 0.0,
                    demand=rec.projected_demand if rec else 0.0,
                    aog=rec.aog_risk_level if rec else 0,
                    cost=float(rec.estimated_cost_impact) if rec else 0.0,
                    crit=crit.canonical_tier if crit else None,
                    ata=attrs.ata_chapter if attrs else None,
                    pclass=attrs.part_class if attrs else None,
                    tier=e.outcome.tier if e else None,
                    has_rec=rec is not None,
                )
            )

        def breakdown(field: str) -> tuple[Breakdown, ...]:
            groups: dict = {}
            for r in rows:
                k = r[field]
                if k is None:
                    continue
                g = groups.setdefault(str(k), dict(count=0, on_hand=0, shortage=0.0))
                g["count"] += 1
                g["on_hand"] += r["on_hand"]
                g["shortage"] += r["shortage"]
            return tuple(
                Breakdown(key=k, count=g["count"], on_hand=g["on_hand"], shortage=g["shortage"])
                for k, g in sorted(groups.items())
            )

        shortfalls = [r for r in rows if r["shortage"] > 0]
        top = sorted(shortfalls, key=lambda r: r["shortage"], reverse=True)[:10]
        return DashboardSummary(
            parts=len(rows),
            total_on_hand=sum(r["on_hand"] for r in rows),
            total_on_hand_value=sum(r["on_hand"] * r["unit_cost"] for r in rows),
            total_shortage=sum(r["shortage"] for r in rows),
            total_projected_demand=sum(r["demand"] for r in rows),
            aog_exposure=sum(1 for r in rows if r["aog"] >= 3),
            open_recommendations=sum(1 for r in rows if r["has_rec"]),
            net_cost_impact=sum(r["cost"] for r in rows),
            by_criticality=breakdown("crit"),
            by_ata=breakdown("ata"),
            by_part_class=breakdown("pclass"),
            by_tier=breakdown("tier"),
            top_shortages=tuple(
                PartShortfall(
                    pn=r["pn"],
                    location=r["loc"],
                    shortage=r["shortage"],
                    on_hand=r["on_hand"],
                    projected_demand=r["demand"],
                )
                for r in top
            ),
        )

    @staticmethod
    def _history_days(dates: list) -> int:
        """Mirror of RecommendationService._history_days (spec §6.1) — span of the
        demand-history observations plus a 30d pad, so a single-bucket history isn't
        mistaken for zero-length."""
        if not dates:
            return 0
        return (max(dates) - min(dates)).days + 30

    @staticmethod
    def _days_in_period(period_start: str) -> int:
        """Real length (in days) of a monthly DEMAND_HISTORY bucket (`bucket="month"`,
        see `extract_loader.build_stores_from_extract`), given its ISO `period_start`
        (always the 1st of the month). Used to scale the portfolio's constant-rate
        demand projection to each period's own length instead of splitting one total
        evenly across periods regardless of how many days they actually cover."""
        d = date.fromisoformat(period_start)
        return calendar.monthrange(d.year, d.month)[1]

    def forecast_summary(self) -> ForecastSummary:
        """Slice S5 — Forecast & Service Levels (PRD §6.6).

        Three honestly-scoped pieces, all derived from the same real per-key data the
        rest of the BFF already loads (self.fs / self.keys — no new data source):

        - service_levels: REAL. `TenantPolicyConfig().service_level_by_tier` (spec
          §5.3) crossed with the real count of keys per `Criticality.canonical_tier`.
          `actual_coverage` is the same honest on-hand-vs-shortage proxy the Overview's
          SlInvestmentPanel uses — not a true fill-rate backtest.
        - method_coverage: REAL. Every key's demand regime is computed with the exact
          deterministic classifier the engine runs (`trax_io_reco.regime.classifier.
          classify`, spec §6.1) over its real `DemandHistory` — cheap (event-count
          arithmetic), so this runs over the full portfolio rather than sampling.
          Regime is then mapped to the forecast method that actually serves it in v1
          (`_REGIME_METHOD`).
        - accuracy: HONEST GAP. No backtest runs at serve time, so this is NOT a MAPE/
          bias metric. It's a labeled proxy: recent real actual demand (from
          DEMAND_HISTORY observations, rolled into the two most recent MONTHLY
          buckets present in the extract — `bucket="month"`, not 90-day) vs. the
          engine's current per-key mean-per-day projection (`projected_demand /
          horizon_days`, summed across the portfolio) scaled to each period's own
          real length in days. This is a constant-rate projection re-scaled per
          period, not a genuine per-period reforecast — if the rendered periods
          happen to be equal-length, the projected values will look flat, which is
          truthful rather than a bug.
        """
        t = self.tenant
        policy_cfg = TenantPolicyConfig()

        by_key: dict[tuple[str, str], _Entry] = {}
        for e in self._entries.values():
            key = (e.rec.part_number, e.rec.current_location)
            if key not in by_key:
                by_key[key] = e

        tier_counts: dict[int, int] = {}
        tier_on_hand: dict[int, int] = {}
        tier_shortage: dict[int, float] = {}
        regime_counts: dict[str, int] = {}
        actual_by_period: dict[str, float] = {}
        mean_per_day_total = 0.0
        actual_total = 0.0

        for pn, loc in self.keys:
            crit = _safe(lambda pn=pn: self.fs.get_criticality(tenant=t, pn=pn))
            if crit is not None:
                tier = crit.canonical_tier
                tier_counts[tier] = tier_counts.get(tier, 0) + 1
                sp = _safe(
                    lambda pn=pn, loc=loc: self.fs.get_stock_position(
                        tenant=t, pn=pn, location=loc
                    )
                )
                e = by_key.get((pn, loc))
                rec = e.rec if e else None
                tier_on_hand[tier] = tier_on_hand.get(tier, 0) + (sp.on_hand if sp else 0)
                tier_shortage[tier] = tier_shortage.get(tier, 0.0) + (
                    rec.shortage_quantity if rec else 0.0
                )

            dh = _safe(
                lambda pn=pn, loc=loc: self.fs.get_demand_history(tenant=t, pn=pn, location=loc)
            )
            if dh is not None and dh.observations:
                events = events_24mo_from(dh)
                dates = [o.period_start for o in dh.observations]
                regime = classify(events_24mo=events, history_days=self._history_days(dates))
                regime_counts[regime.value] = regime_counts.get(regime.value, 0) + 1

                # Honest accuracy proxy: bucket real actual demand by period_start
                # (monthly buckets — see extract_loader.build_stores_from_extract),
                # and separately accumulate the portfolio's current constant-rate
                # demand projection (mean per day) so each rendered period below can
                # be scaled by its own real length instead of splitting one total
                # evenly across periods.
                for o in dh.observations:
                    period_key = o.period_start.isoformat()
                    actual_by_period[period_key] = actual_by_period.get(
                        period_key, 0.0
                    ) + (o.removals + o.issues)

                e = by_key.get((pn, loc))
                if e is not None:
                    actual_total += sum(o.removals + o.issues for o in dh.observations)
                    if e.rec.horizon_days > 0:
                        mean_per_day_total += e.rec.projected_demand / e.rec.horizon_days

        bands = tuple(
            ServiceLevelBand(
                criticality_tier=tier,
                target_service_level=policy_cfg.service_level_by_tier.get(tier, 0.0),
                sku_count=tier_counts.get(tier, 0),
                actual_coverage=(
                    None
                    if tier not in tier_counts
                    else (
                        1.0
                        if (tier_on_hand[tier] + tier_shortage[tier]) == 0
                        else tier_on_hand[tier]
                        / (tier_on_hand[tier] + tier_shortage[tier])
                    )
                ),
            )
            for tier in sorted(policy_cfg.service_level_by_tier)
        )

        total_skus = sum(regime_counts.values())
        coverage_rows = tuple(
            MethodCoverageRow(
                regime=regime,
                method=_REGIME_METHOD.get(regime, "Unclassified"),
                sku_count=count,
                pct=(count / total_skus) if total_skus else 0.0,
            )
            for regime, count in sorted(regime_counts.items())
        )

        # Recent-vs-projected accuracy proxy, bucketed by the (at most) two most
        # recent distinct period_start values present in the extract — an honest
        # "last observed period(s) vs current projection" comparison, not a backtest.
        # Each period gets its OWN projected value: the portfolio's current
        # constant-rate projection (mean_per_day_total) scaled by that period's
        # real length in days, not one total split evenly across periods.
        recent_periods = sorted(actual_by_period)[-2:]
        accuracy_points = tuple(
            AccuracyPoint(
                period_start=period,
                actual=actual_by_period[period],
                projected=mean_per_day_total * self._days_in_period(period),
            )
            for period in recent_periods
        )

        return ForecastSummary(
            service_levels=ServiceLevelPolicy(bands=bands),
            method_coverage=MethodCoverage(total_skus=total_skus, rows=coverage_rows),
            accuracy=ForecastAccuracy(
                status="proxy",
                note=(
                    "No backtest runs at serve time. Points compare real recent "
                    "monthly DEMAND_HISTORY actuals against the engine's current "
                    "constant-rate (mean-per-day) demand projection scaled to each "
                    "period's own length — an honest proxy, not a per-period "
                    "reforecast or a MAPE/bias backtest."
                ),
                points=accuracy_points,
            ),
        )

    # ----------------------------------------------------------------------- #
    # Slice S7 — Data & Connections / feed health (PRD §6.7)
    # ----------------------------------------------------------------------- #
    def _manifest_artifact_status(self) -> dict[str, str]:
        return {
            a.get("domain"): a.get("status")
            for a in self._manifest.get("artifacts", [])
            if isinstance(a, dict) and a.get("domain")
        }

    def _manifest_row_count(self, domain: str) -> int | None:
        """`row_count` per domain when the manifest carries it (the committed sample
        manifest does not — see `bff/feeds.py`/`FeedHealthRow` docstring) — never
        fabricated, always `None` when absent rather than guessed from `self.keys`."""
        for a in self._manifest.get("artifacts", []):
            if isinstance(a, dict) and a.get("domain") == domain:
                count = a.get("row_count")
                return int(count) if isinstance(count, (int, float)) else None
        return None

    def feeds_summary(self) -> FeedsSummary:
        """Slice S7 — Data & Connections (PRD §6.7): the honest feed-health surface.

        Every row's `status`/`domains`/`notes` come from the static, code-verified
        mapping in `bff/feeds.py` (cross-checked against `domains.py` and
        `extract_loader.py` — see that module's docstring for the per-feed evidence).
        `rows` is the manifest artifact's `row_count` when present (the committed
        sample manifest has none, so this is `None` there — not fabricated from
        `len(self.keys)`, which is a recommendation-engine key count, not a raw
        per-domain extract row count). `last_sync` is the manifest's `extract_date`
        for every feed with at least one connected/partial domain, else `None`.

        If `self._manifest` is empty (extract dir had no manifest.json, or a
        corrupt/unreadable one), every feed's truthful status/domains/notes still
        render exactly as they do with a manifest — only `rows`/`last_sync` degrade to
        `None`, per the task's degrade-gracefully requirement.
        """
        artifact_status = self._manifest_artifact_status()
        extract_date = self._manifest.get("extract_date")

        rows: list[FeedHealthRow] = []
        for d in FEED_DEFINITIONS:
            # A feed's domains might not all appear in a trimmed/partial manifest —
            # only claim a `last_sync` when the manifest actually attests to at least
            # one of this feed's backing domains having run.
            domain_seen = any(dom in artifact_status for dom in d.domains)
            row_counts = [
                c for dom in d.domains if (c := self._manifest_row_count(dom)) is not None
            ]
            rows.append(
                FeedHealthRow(
                    feed_id=d.feed_id,
                    name=d.name,
                    status=d.status,
                    domains=d.domains,
                    rows=(sum(row_counts) if row_counts else None),
                    last_sync=(extract_date if (domain_seen and extract_date) else None),
                    notes=d.notes,
                )
            )

        connected = sum(1 for r in rows if r.status is FeedConnectionStatus.CONNECTED)
        partial = sum(1 for r in rows if r.status is FeedConnectionStatus.PARTIAL)
        not_connected = sum(1 for r in rows if r.status is FeedConnectionStatus.NOT_CONNECTED)

        return FeedsSummary(
            health=FeedHealthStrip(
                connected=connected,
                partial=partial,
                not_connected=not_connected,
                extract_date=extract_date,
            ),
            feeds=tuple(rows),
        )

    # ----------------------------------------------------------------------- #
    # Slice S6 — What-If Scenarios (PRD §6.5)
    # ----------------------------------------------------------------------- #
    def _key_stats(self) -> list[KeyStats]:
        """Memoized per-key demand/lead-time/cost primitives — built once per store
        instance from the real `fs`/`keys`, reused across every `solve_scenario` call
        (including all 7 frontier points of a single solve) so repeated slider drags
        don't re-derive them (spec: solver must stay interactive over 22.9K keys)."""
        if self._key_stats_cache is None:
            self._key_stats_cache = build_key_stats(fs=self.fs, tenant=self.tenant, keys=self.keys)
        return self._key_stats_cache

    @staticmethod
    def _to_solver_params(wire: ScenarioParamsWire) -> ScenarioParams:
        return ScenarioParams(
            service_level_target=wire.service_level_target,
            service_level_by_tier=dict(wire.service_level_by_tier),
            budget_cap=wire.budget_cap,
            lead_time_delta_pct=wire.lead_time_delta_pct,
            scope=wire.scope.value,
            scope_value=wire.scope_value,
        )

    @staticmethod
    def _outcome_wire(o) -> ScenarioOutcomeWire:
        return ScenarioOutcomeWire(
            service_level=o.service_level,
            projected_investment=o.projected_investment,
            projected_coverage=o.projected_coverage,
            on_hand_gap_ratio=o.on_hand_gap_ratio,
            scored_keys=o.scored_keys,
        )

    def _result_wire(self, params: ScenarioParamsWire, result: SolveResult) -> ScenarioSolveResult:
        return ScenarioSolveResult(
            params=params,
            current=self._outcome_wire(result.current),
            proposed=self._outcome_wire(result.proposed),
            delta_investment=result.delta_investment,
            delta_coverage=result.delta_coverage,
            frontier=tuple(
                FrontierPointWire(
                    service_level=p.service_level,
                    projected_investment=p.projected_investment,
                    projected_coverage=p.projected_coverage,
                )
                for p in result.frontier
            ),
            skipped_keys=result.skipped_keys,
            total_keys=result.total_keys,
            budget_cap_binds=result.budget_cap_binds,
        )

    def solve_scenario(self, params: ScenarioParamsWire) -> ScenarioSolveResult:
        """`POST .../scenarios/solve` — live solve, not persisted (API-SPEC.md)."""
        solver = ScenarioSolver(self._key_stats(), total_keys_in_universe=len(self.keys))
        result = solver.solve(self._to_solver_params(params))
        return self._result_wire(params, result)

    def save_scenario(
        self, name: str, params: ScenarioParamsWire, result: ScenarioSolveResult
    ) -> Scenario:
        scenario = Scenario(
            id=str(uuid.uuid4()),
            name=name,
            params=params,
            result=result,
            status=ScenarioStatus.DRAFT,
            created_at=datetime.now(UTC),
        )
        self._scenarios[scenario.id] = _ScenarioEntry(scenario)
        return scenario

    def list_scenarios(self) -> list[Scenario]:
        return sorted(
            (e.scenario for e in self._scenarios.values()),
            key=lambda s: s.created_at,
            reverse=True,
        )

    def _get_scenario_entry(self, scenario_id: str) -> _ScenarioEntry:
        entry = self._scenarios.get(scenario_id)
        if entry is None:
            raise ScenarioNotFound(scenario_id)
        return entry

    def get_scenario(self, scenario_id: str) -> Scenario:
        return self._get_scenario_entry(scenario_id).scenario

    def delete_scenario(self, scenario_id: str) -> None:
        self._get_scenario_entry(scenario_id)  # raises ScenarioNotFound if absent
        del self._scenarios[scenario_id]

    def commit_scenario(self, scenario_id: str) -> ScenarioAuditEvent:
        """Promote a saved scenario to COMMITTED + append an audited marker.

        Does NOT write policies back to eMRO — Writeback is the only agent with eMRO
        write permission (CLAUDE.md cross-cutting rule); a scenario commit is a
        planning-tool decision record, not a policy write. See `ScenarioAuditEvent`.
        """
        entry = self._get_scenario_entry(scenario_id)
        now = datetime.now(UTC)
        committed = entry.scenario.model_copy(
            update={"status": ScenarioStatus.COMMITTED, "committed_at": now}
        )
        entry.scenario = committed
        event = ScenarioAuditEvent(
            scenario_id=scenario_id, scenario_name=committed.name, action="commit", at=now
        )
        self._audit_log.append(event)
        return event

    def scenario_audit_log(self) -> list[ScenarioAuditEvent]:
        return list(self._audit_log)
