# #7 Planner UI — BFF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI backend-for-frontend in `agent-spine/bff/` that exposes the pending-recommendation queue, provenance detail, approve/reject/defer/bulk-approve, writeback history + rollback, and a per-tenant kill switch — over an in-memory `PlannerStore` driven by the real Supervisor pipeline.

**Architecture:** `PlannerStore.from_extract` runs `RecommendationService` + `GuardrailEnforcer`, keeping the `(Recommendation, GuardrailOutcome)` pairs the Supervisor discards. A lifecycle state machine (`PENDING/APPROVED/REJECTED/DEFERRED`) sits over them; approvals flow through `InMemoryWritebackTarget` (history + rollback). `create_planner_app(stores)` wraps the store in FastAPI.

**Tech Stack:** Python 3.14, FastAPI (the `[bff]` extra), pydantic v2, typer, uv + pytest + ruff. No new dependency beyond fastapi.

## Global Constraints

- **Python ≥3.12, runs on 3.14.** All work in `services/agent-spine`, new package `trax_io_spine.bff`. Test: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff`; lint `uv run --extra dev ruff check .` (line-length 100, select E/F/I/B/UP/N/SIM).
- **All BFF models** are pydantic v2 `ConfigDict(frozen=True, extra="forbid")`.
- **Reuse, do not reimplement:** `build_stores_from_extract`, `RecommendationService`, `GuardrailEnforcer`, `to_writeback_request`, `InMemoryWritebackTarget` — the BFF orchestrates them and keeps the `(rec, outcome)` pairs. Do not change the engine, guardrail, supervisor, or writeback.
- **The `Recommendation` contract exposes** (verified): `recommendation_id, tenant_id, type (RecommendationType), part_number, description, current_location, recommended_location, current_stock, projected_demand (float), shortage_quantity, recommended_quantity, estimated_cost_impact (Decimal), aog_risk_level (AogRiskLevel), criticality_tier (int), reason, supporting_evidence (tuple[Evidence{kind,ref_id,detail,as_of}]), confidence_score, horizon_days, suggested_autonomy_tier (AutonomyTier), guardrail_flags, generated_at, input_snapshot_hash, policy (PolicyRecommendation | None), current_policy (CurrentPolicy | None)`. There is **no** nested `DemandProjection` on the recommendation — use `projected_demand` (the scalar) + `policy`/`current_policy`/`supporting_evidence`.
- **Signatures (verified):** `build_stores_from_extract(extract_dir, *, tenant_id=None) -> (fs, inv, tid, keys)`; `RecommendationService(feature_store=, inventory_state=).run(*, tenant, keys, now, reporting_horizon_days=30) -> RecommendationBatch` (`.recommendations: tuple[Recommendation,...]`); `GuardrailEnforcer().enforce(rec) -> GuardrailOutcome{recommendation_id, status (GuardrailStatus), tier (AutonomyTier), delta_pct (float), reasons (tuple[str,...]), approval_task (ApprovalTask|None)}`; `to_writeback_request(rec, *, idempotency_key, tier=None, shadow=False) -> WritebackRequest` (from `trax_io_spine.supervisor`); `InMemoryWritebackTarget().write/get_history/rollback`.
- Idempotency key matches the supervisor: `f"{tenant_id}:{pn}:{location}:{input_snapshot_hash}"`.
- Commit after each task with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: BFF models + `[bff]` extra

**Files:**
- Modify: `services/agent-spine/pyproject.toml` (add a `[bff]` extra = `fastapi`)
- Create: `services/agent-spine/src/trax_io_spine/bff/__init__.py` (empty)
- Create: `services/agent-spine/src/trax_io_spine/bff/models.py`
- Test: `services/agent-spine/tests/bff/__init__.py` (empty), `services/agent-spine/tests/bff/test_models.py`

**Interfaces:** Produces `TaskStatus`, `RejectReason` (StrEnums); `QueueRow`, `RecommendationDetail`, `RejectRequest`, `DeferRequest`, `BulkApproveFilter`, `ActionResult`, `KillSwitchState` (frozen models).

- [ ] **Step 1: Add the extra** — in `services/agent-spine/pyproject.toml`, add to `[project.optional-dependencies]`: `bff = ["fastapi>=0.115"]`. Run `uv sync --extra dev --extra bff`.

- [ ] **Step 2: Write the failing test** — `tests/bff/test_models.py`

```python
from decimal import Decimal

from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType

from trax_io_spine.bff.models import (
    BulkApproveFilter,
    KillSwitchState,
    QueueRow,
    RejectReason,
    RejectRequest,
    TaskStatus,
)


def test_queue_row_round_trips():
    row = QueueRow(
        recommendation_id="r1", pn="P1", location="YYZ", type=RecommendationType.PURCHASE,
        criticality_tier=2, aog_risk_level=AogRiskLevel.LOW, confidence_score=0.8,
        recommended_quantity=4.0, estimated_cost_impact=Decimal("1200.50"),
        tier=AutonomyTier.BOUNDED, priority_score=12.5, status=TaskStatus.PENDING,
        reason="queued: cost delta exceeds band",
    )
    assert QueueRow.model_validate_json(row.model_dump_json()) == row


def test_reject_request_defaults_and_enum():
    r = RejectRequest(reason=RejectReason.WRONG_FOR_FLEET)
    assert r.detail == ""
    assert r.reason.value == "wrong_for_fleet"


def test_bulk_filter_all_optional():
    f = BulkApproveFilter()
    assert f.tiers is None and f.max_delta_pct is None and f.criticality_min is None


def test_kill_switch_state():
    assert KillSwitchState(engaged=True).engaged is True
```

- [ ] **Step 3: Run it, verify it fails** — `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/test_models.py -v` → FAIL.

- [ ] **Step 4: Implement `models.py`**

```python
"""Planner-UI BFF wire models — mirror the engine/spine contracts for the future TS client."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType

from trax_io_spine.contracts import WritebackResult


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class RejectReason(StrEnum):
    WRONG_FOR_FLEET = "wrong_for_fleet"
    WRONG_ESSENTIALITY = "wrong_essentiality"
    BAD_LEAD_TIME = "bad_lead_time"
    PLANNER_OVERRIDE = "planner_override"
    OTHER = "other"


class QueueRow(_Base):
    recommendation_id: str
    pn: str
    location: str
    type: RecommendationType
    criticality_tier: int
    aog_risk_level: AogRiskLevel
    confidence_score: float
    recommended_quantity: float
    estimated_cost_impact: Decimal
    tier: AutonomyTier
    priority_score: float
    status: TaskStatus
    reason: str


class _PolicyView(_Base):
    rop: int
    eoq: int
    safety_stock: int
    max_stock: int


class _EvidenceView(_Base):
    kind: str
    ref_id: str
    detail: str
    as_of: str | None = None


class RecommendationDetail(_Base):
    recommendation_id: str
    pn: str
    location: str
    type: RecommendationType
    criticality_tier: int
    aog_risk_level: AogRiskLevel
    confidence_score: float
    recommended_quantity: float
    estimated_cost_impact: Decimal
    tier: AutonomyTier
    status: TaskStatus
    reason: str
    provenance_id: str | None
    projected_demand: float
    current_policy: _PolicyView | None
    proposed_policy: _PolicyView | None
    supporting_evidence: tuple[_EvidenceView, ...]
    guardrail_flags: tuple[str, ...]


class RejectRequest(_Base):
    reason: RejectReason
    detail: str = ""


class DeferRequest(_Base):
    until: datetime | None = None


class BulkApproveFilter(_Base):
    tiers: tuple[AutonomyTier, ...] | None = None
    max_delta_pct: float | None = None
    criticality_min: int | None = None
    types: tuple[RecommendationType, ...] | None = None


class ActionResult(_Base):
    recommendation_id: str
    status: TaskStatus
    writeback: WritebackResult | None = None
    message: str = ""


class KillSwitchState(_Base):
    engaged: bool
```

- [ ] **Step 5: Run tests, verify pass + ruff clean.**

- [ ] **Step 6: Commit** — `git add -A services/agent-spine/pyproject.toml services/agent-spine/src/trax_io_spine/bff services/agent-spine/tests/bff && git commit -m "#7 bff: wire models + [bff] extra"`

---

### Task 2: `PlannerStore` — seed + read paths (queue, detail)

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bff/store.py`
- Test: `services/agent-spine/tests/bff/test_store_reads.py`

**Interfaces:** Produces `KillSwitchEngaged(Exception)`, `RecommendationNotFound(Exception)`; `PlannerStore` with `tenant_id`, classmethod `from_extract(*, tenant_id, extract_dir, now, writeback=None)`, `queue(*, status=TaskStatus.PENDING, limit=50) -> list[QueueRow]` (priority-desc), `detail(rec_id) -> RecommendationDetail`.

- [ ] **Step 1: Write the failing test** — `tests/bff/test_store_reads.py`

```python
from datetime import UTC, datetime
from pathlib import Path

from trax_io_spine.bff.models import TaskStatus
from trax_io_spine.bff.store import PlannerStore, RecommendationNotFound
import pytest

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def test_queue_returns_pending_rows_priority_desc():
    rows = _store().queue()
    assert len(rows) >= 1
    assert all(r.status is TaskStatus.PENDING for r in rows)
    scores = [r.priority_score for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_detail_returns_full_provenance():
    store = _store()
    rec_id = store.queue()[0].recommendation_id
    d = store.detail(rec_id)
    assert d.recommendation_id == rec_id
    assert d.projected_demand >= 0.0
    # a queued rec either has a proposed policy (approvable) or not (non_policy)
    assert d.proposed_policy is None or d.proposed_policy.rop >= 0


def test_detail_unknown_id_raises():
    with pytest.raises(RecommendationNotFound):
        _store().detail("nope")


def test_limit_caps_rows():
    assert len(_store().queue(limit=1)) <= 1
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement `store.py`** (seed + reads; actions arrive in Tasks 3–4)

```python
"""In-memory Planner store: the approval queue + lifecycle over the real Supervisor pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trax_io_feature_store import TenantContext
from trax_io_reco.contracts.recommendation import Recommendation
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.service import RecommendationService

from trax_io_spine.bff.models import (
    QueueRow,
    RecommendationDetail,
    TaskStatus,
    _EvidenceView,
    _PolicyView,
)
from trax_io_spine.contracts import GuardrailOutcome, GuardrailStatus
from trax_io_spine.guardrail.enforce import GuardrailEnforcer
from trax_io_spine.supervisor import to_writeback_request
from trax_io_spine.writeback.target import InMemoryWritebackTarget


class KillSwitchEngaged(Exception):
    """Raised when an approve/bulk-approve is attempted while the kill switch is engaged."""


class RecommendationNotFound(Exception):
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


@dataclass
class PlannerStore:
    tenant_id: str
    writeback: InMemoryWritebackTarget = field(default_factory=InMemoryWritebackTarget)
    kill_switch: bool = False
    _entries: dict[str, _Entry] = field(default_factory=dict)

    @classmethod
    def from_extract(
        cls, *, tenant_id: str, extract_dir: str, now: datetime,
        writeback: InMemoryWritebackTarget | None = None,
    ) -> PlannerStore:
        fs, inv, tid, keys = build_stores_from_extract(extract_dir, tenant_id=tenant_id)
        batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
            tenant=TenantContext(tenant_id=tid), keys=keys, now=now
        )
        store = cls(tenant_id=tid, writeback=writeback or InMemoryWritebackTarget())
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
        )

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
        )
```

- [ ] **Step 4: Run tests, verify pass + ruff clean** — `uv run --extra dev --extra bff pytest tests/bff -q`.

- [ ] **Step 5: Commit** — `git commit -m "#7 bff: PlannerStore seed-from-extract + queue + provenance detail"`

---

### Task 3: `PlannerStore` actions — approve / reject / defer + kill switch

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py`
- Test: `services/agent-spine/tests/bff/test_store_actions.py`

**Interfaces:** Adds `approve(rec_id) -> ActionResult`, `reject(rec_id, reason, detail="") -> ActionResult`, `defer(rec_id, until=None) -> ActionResult`, `set_kill_switch(engaged) -> None`. `approve` while `kill_switch` raises `KillSwitchEngaged`; `approve` a rec with no `policy` raises `ValueError`.

- [ ] **Step 1: Write the failing test** — `tests/bff/test_store_actions.py`

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.models import RejectReason, TaskStatus
from trax_io_spine.bff.store import KillSwitchEngaged, PlannerStore
from trax_io_spine.contracts import WritebackStatus

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def _ids_by_policy(store):
    with_p, without_p = [], []
    for row in store.queue():
        (with_p if store.detail(row.recommendation_id).proposed_policy else without_p).append(
            row.recommendation_id
        )
    return with_p, without_p


def test_approve_writes_and_flips_status():
    store = _store()
    with_p, _ = _ids_by_policy(store)
    res = store.approve(with_p[0])
    assert res.status is TaskStatus.APPROVED
    assert res.writeback is not None and res.writeback.status is WritebackStatus.WRITTEN
    assert store.detail(with_p[0]).status is TaskStatus.APPROVED
    assert len(store.writeback.get_history(
        tenant_id="acme", pn=res.writeback.pn, location=res.writeback.location)) == 1


def test_approve_no_policy_rec_raises():
    store = _store()
    _, without_p = _ids_by_policy(store)
    if not without_p:
        pytest.skip("sample produced no non-policy queued recs")
    with pytest.raises(ValueError):
        store.approve(without_p[0])


def test_reject_records_reason():
    store = _store()
    rec_id = store.queue()[0].recommendation_id
    res = store.reject(rec_id, RejectReason.WRONG_FOR_FLEET, "not for this fleet")
    assert res.status is TaskStatus.REJECTED
    assert store.detail(rec_id).status is TaskStatus.REJECTED


def test_defer_sets_status():
    store = _store()
    rec_id = store.queue()[0].recommendation_id
    assert store.defer(rec_id).status is TaskStatus.DEFERRED
    assert store.detail(rec_id).status is TaskStatus.DEFERRED


def test_approve_while_killswitch_engaged_raises():
    store = _store()
    with_p, _ = _ids_by_policy(store)
    store.set_kill_switch(True)
    with pytest.raises(KillSwitchEngaged):
        store.approve(with_p[0])
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement** — add to `PlannerStore` (import `ActionResult`, `RejectReason` in the models import):

```python
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
        return ActionResult(recommendation_id=rec_id, status=TaskStatus.REJECTED, message=reason.value)

    def defer(self, rec_id: str, until: datetime | None = None) -> ActionResult:
        entry = self._get(rec_id)
        entry.status = TaskStatus.DEFERRED
        entry.deferred_until = until
        return ActionResult(recommendation_id=rec_id, status=TaskStatus.DEFERRED, message="deferred")
```

- [ ] **Step 4: Run tests, verify pass + ruff clean** — `uv run --extra dev --extra bff pytest tests/bff -q`.

- [ ] **Step 5: Commit** — `git commit -m "#7 bff: approve/reject/defer + kill switch on PlannerStore"`

---

### Task 4: `PlannerStore` — bulk-approve + history + rollback

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py`
- Test: `services/agent-spine/tests/bff/test_store_bulk.py`

**Interfaces:** Adds `bulk_approve(filter: BulkApproveFilter) -> tuple[int, list[ActionResult]]` (approves matching PENDING entries that have a policy; honors kill switch), `history(*, pn, location) -> tuple[HistoryEntry, ...]`, `rollback(req: RollbackRequest) -> RollbackResult`.

- [ ] **Step 1: Write the failing test** — `tests/bff/test_store_bulk.py`

```python
from datetime import UTC, datetime
from pathlib import Path

from trax_io_spine.bff.models import BulkApproveFilter, TaskStatus
from trax_io_spine.bff.store import KillSwitchEngaged, PlannerStore
from trax_io_spine.contracts import RollbackRequest, RollbackStatus

import pytest

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def test_bulk_approve_approves_policy_bearing_pending():
    store = _store()
    pending_before = len(store.queue())
    count, results = store.bulk_approve(BulkApproveFilter())
    assert count == len(results) >= 1
    assert all(r.status is TaskStatus.APPROVED for r in results)
    assert len(store.queue()) < pending_before  # approved ones left the pending queue


def test_bulk_approve_blocked_by_killswitch():
    store = _store()
    store.set_kill_switch(True)
    with pytest.raises(KillSwitchEngaged):
        store.bulk_approve(BulkApproveFilter())


def test_history_and_rollback_round_trip():
    store = _store()
    count, results = store.bulk_approve(BulkApproveFilter())
    wb = next(r.writeback for r in results if r.writeback is not None)
    hist = store.history(pn=wb.pn, location=wb.location)
    assert len(hist) >= 1
    res = store.rollback(RollbackRequest(
        tenant_id="acme", pn=wb.pn, location=wb.location, reason="planner undo",
        requested_at=datetime.now(UTC),
    ))
    assert res.status in (RollbackStatus.ROLLED_BACK, RollbackStatus.NOTHING_TO_REVERT)
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement** — add to `PlannerStore` (import `BulkApproveFilter`, and `HistoryEntry`, `RollbackRequest`, `RollbackResult` from `trax_io_spine.contracts`):

```python
    def _matches(self, entry: _Entry, f: BulkApproveFilter) -> bool:
        if f.tiers is not None and entry.outcome.tier not in f.tiers:
            return False
        if f.max_delta_pct is not None and entry.outcome.delta_pct > f.max_delta_pct:
            return False
        if f.criticality_min is not None and entry.rec.criticality_tier < f.criticality_min:
            return False
        if f.types is not None and entry.rec.type not in f.types:
            return False
        return True

    def bulk_approve(self, filter: BulkApproveFilter) -> tuple[int, list[ActionResult]]:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
        targets = [
            rid for rid, e in self._entries.items()
            if e.status is TaskStatus.PENDING and e.rec.policy is not None and self._matches(e, filter)
        ]
        results = [self.approve(rid) for rid in targets]
        return len(results), results

    def history(self, *, pn: str, location: str) -> tuple[HistoryEntry, ...]:
        return self.writeback.get_history(tenant_id=self.tenant_id, pn=pn, location=location)

    def rollback(self, req: RollbackRequest) -> RollbackResult:
        return self.writeback.rollback(req)
```

- [ ] **Step 4: Run tests, verify pass + ruff clean** — `uv run --extra dev --extra bff pytest tests/bff -q`.

- [ ] **Step 5: Commit** — `git commit -m "#7 bff: bulk-approve + history + rollback on PlannerStore"`

---

### Task 5: FastAPI app + endpoints

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bff/app.py`
- Test: `services/agent-spine/tests/bff/test_app.py`

**Interfaces:** `create_planner_app(stores: dict[str, PlannerStore]) -> FastAPI` with the endpoints from the spec (all under `/v1/tenants/{tenant_id}`). Unknown tenant/rec → `404`; approve no-policy → `409`; kill-switch-blocked → `423`; bad body → `422`.

- [ ] **Step 1: Write the failing test** — `tests/bff/test_app.py`

```python
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def _client():
    store = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    return TestClient(create_planner_app({"acme": store})), store


def _policy_rec_id(client):
    for row in client.get("/v1/tenants/acme/recommendations").json():
        d = client.get(f"/v1/tenants/acme/recommendations/{row['recommendation_id']}").json()
        if d["proposed_policy"] is not None:
            return row["recommendation_id"]
    raise AssertionError("no policy-bearing rec")


def test_queue_endpoint_priority_desc():
    client, _ = _client()
    rows = client.get("/v1/tenants/acme/recommendations").json()
    assert len(rows) >= 1
    assert [r["priority_score"] for r in rows] == sorted(
        [r["priority_score"] for r in rows], reverse=True
    )


def test_unknown_tenant_404():
    client, _ = _client()
    assert client.get("/v1/tenants/ghost/recommendations").status_code == 404


def test_detail_unknown_rec_404():
    client, _ = _client()
    assert client.get("/v1/tenants/acme/recommendations/nope").status_code == 404


def test_approve_then_history():
    client, _ = _client()
    rid = _policy_rec_id(client)
    assert client.post(f"/v1/tenants/acme/recommendations/{rid}/approve").status_code == 200
    d = client.get(f"/v1/tenants/acme/recommendations/{rid}").json()
    hist = client.get(
        f"/v1/tenants/acme/history?pn={d['pn']}&location={d['location']}"
    ).json()
    assert len(hist) >= 1


def test_reject_body_and_status():
    client, _ = _client()
    rid = client.get("/v1/tenants/acme/recommendations").json()[0]["recommendation_id"]
    r = client.post(
        f"/v1/tenants/acme/recommendations/{rid}/reject",
        json={"reason": "wrong_for_fleet", "detail": "x"},
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_killswitch_blocks_approve_with_423():
    client, _ = _client()
    rid = _policy_rec_id(client)
    assert client.post("/v1/tenants/acme/killswitch", json={"engaged": True}).status_code == 200
    assert client.post(f"/v1/tenants/acme/recommendations/{rid}/approve").status_code == 423


def test_bad_reject_reason_422():
    client, _ = _client()
    rid = client.get("/v1/tenants/acme/recommendations").json()[0]["recommendation_id"]
    r = client.post(
        f"/v1/tenants/acme/recommendations/{rid}/reject", json={"reason": "not_a_reason"}
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run it, verify it fails** — `uv run --extra dev --extra bff pytest tests/bff/test_app.py -v`.

- [ ] **Step 3: Implement `app.py`**

```python
"""FastAPI backend-for-frontend for the Planner UI ('Trax IO Review')."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from trax_io_spine.bff.models import (
    ActionResult,
    BulkApproveFilter,
    DeferRequest,
    KillSwitchState,
    QueueRow,
    RecommendationDetail,
    RejectRequest,
    TaskStatus,
)
from trax_io_spine.bff.store import KillSwitchEngaged, PlannerStore, RecommendationNotFound
from trax_io_spine.contracts import HistoryEntry, RollbackRequest, RollbackResult


def create_planner_app(stores: dict[str, PlannerStore]) -> FastAPI:
    app = FastAPI(title="Trax IO Review — Planner BFF")

    def _store(tenant_id: str) -> PlannerStore:
        store = stores.get(tenant_id)
        if store is None:
            raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
        return store

    base = "/v1/tenants/{tenant_id}"

    @app.get(base + "/recommendations")
    def queue(tenant_id: str, status: TaskStatus = TaskStatus.PENDING, limit: int = 50) -> list[QueueRow]:
        return _store(tenant_id).queue(status=status, limit=limit)

    @app.get(base + "/recommendations/{rec_id}")
    def detail(tenant_id: str, rec_id: str) -> RecommendationDetail:
        try:
            return _store(tenant_id).detail(rec_id)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(base + "/recommendations/{rec_id}/approve")
    def approve(tenant_id: str, rec_id: str) -> ActionResult:
        store = _store(tenant_id)
        try:
            return store.approve(rec_id)
        except KillSwitchEngaged as exc:
            raise HTTPException(status_code=423, detail="kill switch engaged") from exc
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(base + "/recommendations/{rec_id}/reject")
    def reject(tenant_id: str, rec_id: str, body: RejectRequest) -> ActionResult:
        try:
            return _store(tenant_id).reject(rec_id, body.reason, body.detail)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(base + "/recommendations/{rec_id}/defer")
    def defer(tenant_id: str, rec_id: str, body: DeferRequest) -> ActionResult:
        try:
            return _store(tenant_id).defer(rec_id, body.until)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(base + "/recommendations/bulk-approve")
    def bulk_approve(tenant_id: str, body: BulkApproveFilter) -> dict:
        store = _store(tenant_id)
        try:
            count, results = store.bulk_approve(body)
        except KillSwitchEngaged as exc:
            raise HTTPException(status_code=423, detail="kill switch engaged") from exc
        return {"approved_count": count, "results": [r.model_dump(mode="json") for r in results]}

    @app.get(base + "/history")
    def history(tenant_id: str, pn: str, location: str) -> list[HistoryEntry]:
        return list(_store(tenant_id).history(pn=pn, location=location))

    @app.post(base + "/rollback")
    def rollback(tenant_id: str, body: RollbackRequest) -> RollbackResult:
        return _store(tenant_id).rollback(body)

    @app.get(base + "/killswitch")
    def get_killswitch(tenant_id: str) -> KillSwitchState:
        return KillSwitchState(engaged=_store(tenant_id).kill_switch)

    @app.post(base + "/killswitch")
    def set_killswitch(tenant_id: str, body: KillSwitchState) -> KillSwitchState:
        _store(tenant_id).set_kill_switch(body.engaged)
        return body

    return app
```

- [ ] **Step 4: Run tests, verify pass** — `uv run --extra dev --extra bff pytest tests/bff -q`, then the **full** suite `uv run --extra dev --extra emro --extra bff pytest -q` (no regression). ruff clean.

- [ ] **Step 5: Commit** — `git commit -m "#7 bff: FastAPI app + planner endpoints (queue/detail/actions/history/rollback/killswitch)"`

---

## Post-implementation (controller, after final review)

- ADR `docs/adr/2026-06-28-0011-planner-ui-bff.md` (Planner BFF in agent-spine; in-memory store reusing the Supervisor pipeline; React frontend + auth + persistence deferred; the emitted OpenAPI is the contract for the React slice).
- CLAUDE.md: add the `--extra bff` test note + how to run the BFF (`uvicorn` over `create_planner_app`).
- ROADMAP #7: mark the BFF slice done; React frontend + the deferred surfaces (digest/settings/auth/persistence/SSE/bulk-rollback) next.
- TASKS.md session entry. Merge `feat/planner-ui-bff` → main, push, delete branch (restore any unrelated lockfile churn first).

## Self-Review

- **Spec coverage:** §3.3 models → Task 1; §3.1 store seed+reads → Task 2, actions → Tasks 3–4; §3.2 endpoints → Task 5; §4 testing spread across tasks. All covered.
- **Type consistency:** `PlannerStore`/`_Entry`/`TaskStatus`/`ActionResult`/`BulkApproveFilter` consistent across tasks; the app maps store exceptions to `404/409/423` exactly as the spec's status table. `RecommendationDetail` uses the **real** rec fields (`projected_demand` scalar + `policy`/`current_policy`/`supporting_evidence`), not a non-existent nested `DemandProjection`.
- **Placeholders:** none — every step has runnable code. The store seeds from the real pipeline; edge cases (no-policy 409, kill-switch 423) are exercised against actual sample recs (the sample yields both policy-bearing and `non_policy` queued recs).
