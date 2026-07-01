"""In-memory Planner store: the approval queue + lifecycle over the real Supervisor pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trax_io_feature_store import TenantContext
from trax_io_reco.contracts.recommendation import Recommendation
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.service import RecommendationService

from trax_io_spine.bff.models import (
    ActionResult,
    Breakdown,
    BulkApproveFilter,
    DashboardSummary,
    DemandPoint,
    DemandSummary,
    LeadTimeView,
    OpenOrderView,
    PartAttributesView,
    PartContext,
    PartShortfall,
    QueueRow,
    RecommendationDetail,
    RejectReason,
    StockBreakdown,
    TaskStatus,
    _EvidenceView,
    _PolicyView,
)
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


class KillSwitchEngaged(Exception):  # noqa: N818
    """Raised when an approve/bulk-approve is attempted while the kill switch is engaged."""


class RecommendationNotFound(Exception):  # noqa: N818
    """Raised when a recommendation_id is unknown to this tenant's store."""


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


@dataclass
class PlannerStore:
    tenant_id: str
    writeback: InMemoryWritebackTarget = field(default_factory=InMemoryWritebackTarget)
    kill_switch: bool = False
    _entries: dict[str, _Entry] = field(default_factory=dict)
    fs: object | None = None
    tenant: object | None = None
    keys: list = field(default_factory=list)

    @classmethod
    def from_extract(
        cls, *, tenant_id: str, extract_dir: str, now: datetime,
        writeback: InMemoryWritebackTarget | None = None,
        pool_by_part: bool = False,
    ) -> PlannerStore:
        # pool_by_part: network-pooled on-hand/demand for real eMRO extracts (where
        # policies key at planning locations but stock lives at physical ones). Off by
        # default so the committed sample loads per-location exactly as before.
        fs, inv, tid, keys = build_stores_from_extract(
            extract_dir, tenant_id=tenant_id, pool_by_part=pool_by_part
        )
        tenant = TenantContext(tenant_id=tid)
        batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
            tenant=tenant, keys=keys, now=now
        )
        store = cls(tenant_id=tid, writeback=writeback or InMemoryWritebackTarget())
        store.fs = fs
        store.tenant = tenant
        store.keys = list(keys)
        enforcer = GuardrailEnforcer()
        for rec in batch.recommendations:
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

    def approve(self, rec_id: str) -> ActionResult:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
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
        entry = self._get(rec_id)
        entry.status = TaskStatus.REJECTED
        entry.reject_reason = reason.value
        entry.reject_detail = detail
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.REJECTED, message=reason.value
        )

    def defer(self, rec_id: str, until: datetime | None = None) -> ActionResult:
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
        return self.writeback.rollback(req)

    def queue(self, *, status: TaskStatus = TaskStatus.PENDING, limit: int = 50) -> list[QueueRow]:
        entries = [e for e in self._entries.values() if e.status is status]
        entries.sort(key=self._priority, reverse=True)
        return [self._row(e) for e in entries[:limit]]

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

    def dashboard(self) -> DashboardSummary:
        t = self.tenant
        rows = []  # per-key facts
        # NOTE: this loop scans every (pn, location) key and, per key, does an O(n)
        # linear lookup into self._entries to find the matching recommendation.
        # That's O(keys * entries) overall — acceptable only at sample scale (the
        # extract-sample fixture this store is seeded from). Real-portfolio scale
        # needs an indexed lookup (e.g. dict keyed by (pn, location)) and paginated
        # aggregation; deferred per the design spec's dashboard/aggregation scaling
        # notes (docs/design §6).
        for pn, loc in self.keys:
            sp = _safe(
                lambda pn=pn, loc=loc: self.fs.get_stock_position(tenant=t, pn=pn, location=loc)
            )
            attrs = _safe(lambda pn=pn: self.fs.get_part_attributes(tenant=t, pn=pn))
            crit = _safe(lambda pn=pn: self.fs.get_criticality(tenant=t, pn=pn))
            ve = _safe(
                lambda pn=pn: self.fs.get_vendor_economics(tenant=t, pn=pn, vendor="DEFAULT")
            )
            e = next(
                (
                    x
                    for x in self._entries.values()
                    if x.rec.part_number == pn and x.rec.current_location == loc
                ),
                None,
            )
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
