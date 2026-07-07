# BVR Pipeline (v1-local) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A schema-locked, deterministically generated Business Value Report (projected-only attribution vs the pre-agent extract baseline), rendered as printable HTML (+ optional WeasyPrint PDF), served by three BFF routes, and surfaced in a live Reports section in the web frontend — over the real 58.9K-key deploy.

**Architecture:** New `trax_io_spine.bvr` package (models → attribution → svg → render → pdf; bvr never imports `bff`, decoupled via a `BvrInputs` dataclass). `PlannerStore.bvr()` assembles inputs from what it already retains (feature store, `_entries`, writeback ledger, `_key_stats`) and memoizes; decision actions invalidate. Planner-ui gains `#/reports`.

**Tech Stack:** Python ≥3.12, pydantic v2, jinja2 (new `bvr` extra), WeasyPrint (new `pdf` extra, skip-clean tests), FastAPI, React 18 + Vitest (planner-ui).

**Spec:** [docs/superpowers/specs/2026-07-02-bvr-pipeline-v1-local-design.md](../specs/2026-07-02-bvr-pipeline-v1-local-design.md)

## Global Constraints

- **Projected-only honesty contract:** every monetary figure labeled projected; applied (WRITTEN) vs shadowed (SHADOWED) totals never blended without the split; unvalued changes counted, never silently dropped.
- **Exact default rates (spec §2):** `holding_cost_rate=0.25`/yr, `per_order_cost=85.0`, `stockout_proxy_fraction=0.10`, tier weights `{1: 1.0, 2: 0.8, 3: 0.6, 4: 0.4, 5: 0.2}`, `period_fraction=1/12`. §5.5 tier fill-rate targets `{1: 0.995, 2: 0.98, 3: 0.95, 4: 0.92, 5: 0.90}`.
- **Money:** compute in float, quantize each reported total once via `Decimal(str(x)).quantize(Decimal("0.01"))`. Positive = projected benefit; negatives reported as-is, never clamped.
- **`schema_version = "1.0.0"`** on `BvrReport`; additive change ⇒ minor bump (schema-lock test enforces awareness).
- **No Chart.js/pyppeteer** — charts are inline SVG from pure helpers.
- Suites: agent-spine `uv run --extra dev --extra bff --extra bvr pytest` (+ `--extra pdf` where noted) + ruff; planner-ui `npm test` + `npm run build`; all existing tests stay green; lines ≤100 chars.
- Docker scoped to project `trax-io-planner`; NEVER touch `oracle`/`oracle19c`/MySQL; single sequential builds; real data stays gitignored.
- Commit prefix `#8`.

## File Structure

- Create: `services/agent-spine/src/trax_io_spine/bvr/{__init__.py,models.py,attribution.py,svg.py,render.py,pdf.py}` + `bvr/templates/bvr.html.j2`
- Create: `services/agent-spine/tests/bvr/{__init__.py,test_models.py,test_attribution.py,test_report.py,test_render.py,test_pdf.py}`
- Modify: `services/agent-spine/pyproject.toml` (extras `bvr`, `pdf`), `src/trax_io_spine/writeback/target.py` (`iter_history`), `tests/test_writeback.py`-adjacent (new test file ok), `src/trax_io_spine/bff/store.py` (`bvr()` + cache + invalidation), `src/trax_io_spine/bff/app.py` (3 routes), `tests/bff/test_reports.py` (new)
- Modify: `apps/planner-ui/src/{api/types.ts,api/client.ts,api/sample.ts,components/NavRail.tsx,App.tsx}`; Create: `apps/planner-ui/src/components/ReportsView.tsx` + `.module.css` + `.test.tsx`
- Modify (ops): `deploy/bff.Dockerfile`, `apps/planner-ui/UAT.md`, `ROADMAP.md`, `TASKS.md`, `CLAUDE.md`

---

### Task 1: `bvr/models.py` — the locked schema

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bvr/__init__.py` (empty)
- Create: `services/agent-spine/src/trax_io_spine/bvr/models.py`
- Test: `services/agent-spine/tests/bvr/__init__.py` (empty), `services/agent-spine/tests/bvr/conftest.py` (the shared `bvr_report` fixture — Tasks 5–6 reuse it), `services/agent-spine/tests/bvr/test_models.py`

**Interfaces:**
- Consumes: nothing project-internal (pure pydantic).
- Produces (later tasks import these exact names from `trax_io_spine.bvr.models`): `SCHEMA_VERSION = "1.0.0"`, `BvrReport`, `BvrPeriod`, `ExecutiveSummary`, `ProjectedComponent`, `SavingsAttribution`, `TierPosture`, `ServicePosture`, `Governance`, `ForwardOpportunity`, `ForwardLook`, `Methodology`.

- [ ] **Step 1: Write the failing tests**

Create `services/agent-spine/tests/bvr/conftest.py` — the report fixture lives here so
Tasks 5–6 (render/pdf tests) can reuse it WITHOUT cross-test-module imports:

```python
"""Shared BVR test fixture: one fully-populated BvrReport."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest


@pytest.fixture
def bvr_report():
    from trax_io_spine.bvr.models import (
        SCHEMA_VERSION,
        BvrPeriod,
        BvrReport,
        ExecutiveSummary,
        ForwardLook,
        ForwardOpportunity,
        Governance,
        Methodology,
        ProjectedComponent,
        SavingsAttribution,
        ServicePosture,
        TierPosture,
    )

    def component(name: str, amount: str) -> ProjectedComponent:
        return ProjectedComponent(
            name=name, amount=Decimal(amount),
            formula="Δ(safety_stock + EOQ/2) × unit_cost × holding_rate × period_fraction",
            inputs={"changes": 1}, assumptions=("holding_cost_rate=0.25/yr",),
        )

    return BvrReport(
        schema_version=SCHEMA_VERSION,
        tenant_id="acme",
        period=BvrPeriod(
            extract_date="2024-04-01", decision_window_start=None,
            decision_window_end=None, generated_at=datetime(2026, 4, 1, tzinfo=UTC),
            label="Snapshot 2024-04-01",
        ),
        executive_summary=ExecutiveSummary(
            total_projected=Decimal("51.39"), changes_applied=1, changes_shadowed=0,
            keys_under_management=1, open_pipeline_value=Decimal("100.00"),
            service_headline="1/1 tiers at target posture",
        ),
        savings=SavingsAttribution(
            holding_cost_delta=component("holding_cost_delta", "-14.58"),
            ordering_cost_delta=component("ordering_cost_delta", "64.64"),
            stockout_risk_delta=component("stockout_risk_delta", "1.33"),
            total_projected_applied=Decimal("51.39"),
            total_projected_shadowed=Decimal("0.00"),
            total_projected=Decimal("51.39"),
            changes_total=1, changes_valued=1,
            assumption_rates={
                "holding_cost_rate": 0.25, "per_order_cost": 85.0,
                "stockout_proxy_fraction": 0.10, "period_fraction": 1 / 12,
            },
        ),
        service_posture=ServicePosture(
            tiers=(
                TierPosture(tier=1, target_fill_rate=0.995, keys=1,
                            keys_at_posture=1, posture_rate=1.0),
            ),
            note="Posture (ROP covers mean lead-time demand), not realized fill rate.",
        ),
        governance=Governance(
            recommendations_total=2, pending=1, approved=1, rejected=0, deferred=0,
            approval_rate=0.5, override_rate=0.0,
            writes_written=1, writes_shadowed=0, writes_failed=0,
            writes_deferred_open_order=0, rollbacks=0,
            tier_mix={"A": 0, "B": 1, "C": 0}, kill_switch_engaged=False,
        ),
        forward_look=ForwardLook(
            open_pipeline_value=Decimal("100.00"),
            projected_demand_horizon=12.5,
            top_opportunities=(
                ForwardOpportunity(pn="PN1", location="YYZ", type="purchase",
                                   estimated_cost_impact=Decimal("100.00")),
            ),
        ),
        methodology=Methodology(
            formulas=("holding: Δ(ss + EOQ/2) × unit_cost × 0.25/yr × 1/12",),
            assumption_rates={"holding_cost_rate": 0.25},
            ledger_entries=1, recommendations=2, keys=1,
            input_snapshot_hashes=("abc123",),
            agent_version="spine-0.1.0", generated_by="trax_io_spine.bvr",
        ),
    )
```

Create `services/agent-spine/tests/bvr/test_models.py`:

```python
"""Schema-lock tests for the BVR report models (spec §1).

The BvrReport IS the 'BVR schema locked' deliverable: the field-set snapshot
below must be updated deliberately (with a schema_version bump for additive
changes), never accidentally.
"""

from __future__ import annotations

from trax_io_spine.bvr.models import (
    SCHEMA_VERSION,
    BvrReport,
    ProjectedComponent,
    SavingsAttribution,
)


def test_report_round_trips_and_is_frozen(bvr_report):
    r = bvr_report
    assert BvrReport.model_validate(r.model_dump(mode="json")).model_dump() == r.model_dump()


def test_schema_version_is_semver_1_0_0(bvr_report):
    assert SCHEMA_VERSION == "1.0.0"
    assert bvr_report.schema_version == "1.0.0"


def test_schema_lock_field_snapshot():
    # Deliberate-change tripwire: adding/removing/renaming report fields must
    # update this snapshot AND bump SCHEMA_VERSION (additive => minor).
    assert set(BvrReport.model_fields) == {
        "schema_version", "tenant_id", "period", "executive_summary", "savings",
        "service_posture", "governance", "forward_look", "methodology",
    }
    assert set(SavingsAttribution.model_fields) == {
        "holding_cost_delta", "ordering_cost_delta", "stockout_risk_delta",
        "total_projected_applied", "total_projected_shadowed", "total_projected",
        "changes_total", "changes_valued", "assumption_rates",
    }
    assert set(ProjectedComponent.model_fields) == {
        "name", "amount", "formula", "inputs", "assumptions",
    }
```

- [ ] **Step 2: Run to verify failure**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv run --no-sync --extra dev --extra bff pytest tests/bvr/test_models.py -q
```

Expected: FAIL at import — `ModuleNotFoundError: No module named 'trax_io_spine.bvr'`.

- [ ] **Step 3: Implement**

Create `services/agent-spine/src/trax_io_spine/bvr/__init__.py` (empty) and `services/agent-spine/src/trax_io_spine/bvr/models.py`:

```python
"""BVR report schema — the 'BVR schema locked' deliverable (spec §1).

Frozen pydantic models; `SCHEMA_VERSION` is semver (additive change => minor).
Every monetary figure in this schema is PROJECTED (spec honesty contract):
computed from the writeback ledger vs the pre-agent extract baseline with
disclosed formulas — never presented as realized savings.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1.0.0"


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BvrPeriod(_Base):
    extract_date: str | None  # the snapshot's extract_date (ISO date string)
    decision_window_start: datetime | None  # min ledger changed_at (None: no writes)
    decision_window_end: datetime | None  # max ledger changed_at
    generated_at: datetime
    label: str  # e.g. "Snapshot 2024-04-01"


class ExecutiveSummary(_Base):
    total_projected: Decimal
    changes_applied: int  # WRITTEN ledger entries
    changes_shadowed: int  # SHADOWED ledger entries
    keys_under_management: int
    open_pipeline_value: Decimal
    service_headline: str  # e.g. "3/5 tiers at target posture"


class ProjectedComponent(_Base):
    name: str
    amount: Decimal  # positive = projected benefit; negatives reported as-is
    formula: str  # human-readable, restated in Methodology
    inputs: dict[str, float | int]
    assumptions: tuple[str, ...]


class SavingsAttribution(_Base):
    holding_cost_delta: ProjectedComponent
    ordering_cost_delta: ProjectedComponent
    stockout_risk_delta: ProjectedComponent
    # Applied (WRITTEN) vs shadowed (SHADOWED) are never silently blended:
    total_projected_applied: Decimal
    total_projected_shadowed: Decimal
    total_projected: Decimal  # applied + shadowed
    changes_total: int
    changes_valued: int  # the "N of M valued" coverage disclosure
    assumption_rates: dict[str, float]


class TierPosture(_Base):
    tier: int  # essentiality 1..5
    target_fill_rate: float  # design §5.5
    keys: int
    keys_at_posture: int  # rop >= mean_per_day * lead_mean
    posture_rate: float  # keys_at_posture / keys (0.0 when keys == 0)


class ServicePosture(_Base):
    tiers: tuple[TierPosture, ...]
    note: str  # the posture-not-realized disclosure


class Governance(_Base):
    recommendations_total: int
    pending: int
    approved: int
    rejected: int
    deferred: int
    approval_rate: float  # approved / decided (0.0 when none decided)
    override_rate: float  # rejected / decided
    writes_written: int
    writes_shadowed: int
    writes_failed: int
    writes_deferred_open_order: int
    rollbacks: int  # ledger entries whose provenance_id startswith "rollback:"
    tier_mix: dict[str, int]  # ledger writes by AutonomyTier value ("A"/"B"/"C")
    kill_switch_engaged: bool


class ForwardOpportunity(_Base):
    pn: str
    location: str
    type: str  # RecommendationType value
    estimated_cost_impact: Decimal


class ForwardLook(_Base):
    open_pipeline_value: Decimal  # sum of pending recs' estimated_cost_impact
    projected_demand_horizon: float
    top_opportunities: tuple[ForwardOpportunity, ...]  # impact-ranked, max 10


class Methodology(_Base):
    formulas: tuple[str, ...]
    assumption_rates: dict[str, float]
    ledger_entries: int
    recommendations: int
    keys: int
    input_snapshot_hashes: tuple[str, ...]  # distinct, sorted
    agent_version: str
    generated_by: str


class BvrReport(_Base):
    schema_version: str
    tenant_id: str
    period: BvrPeriod
    executive_summary: ExecutiveSummary
    savings: SavingsAttribution
    service_posture: ServicePosture
    governance: Governance
    forward_look: ForwardLook
    methodology: Methodology
```

- [ ] **Step 4: Run to green + lint**

```bash
uv run --no-sync --extra dev --extra bff pytest tests/bvr/test_models.py -q
uv run --no-sync --extra dev --extra bff ruff check .
```

Expected: 3 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/src/trax_io_spine/bvr services/agent-spine/tests/bvr
git commit -m "#8 bvr: locked report schema (BvrReport 1.0.0, projected-only models)"
```

---

### Task 2: `InMemoryWritebackTarget.iter_history` — tenant-wide ledger enumeration

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/writeback/target.py` (add one method to `InMemoryWritebackTarget`)
- Test: `services/agent-spine/tests/bvr/test_iter_history.py` (new file)

**Interfaces:**
- Consumes: existing `self._history: dict[tuple[str, str, str], list[HistoryEntry]]` (keyed `(tenant_id, pn, location)`).
- Produces: `iter_history(tenant_id: str) -> tuple[HistoryEntry, ...]` — ALL entries for the tenant, sorted by `(pn, location, version)`. Task 3's `collect_ledger` calls exactly this. (`fake_emro` is backed by this class, so it inherits the method; `RestWritebackClient` deliberately does not get it — v1-local BVR runs on in-memory targets, documented in the method docstring.)

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/bvr/test_iter_history.py`:

```python
"""iter_history: tenant-wide ledger enumeration for the BVR (spec §2 inputs)."""

from __future__ import annotations

from trax_io_spine.contracts import WritebackRequest
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(tenant: str, pn: str, loc: str, rop: int) -> WritebackRequest:
    return WritebackRequest(
        tenant_id=tenant, pn=pn, location=loc, rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id=f"prov-{pn}-{loc}-{rop}", idempotency_key=None,
        tier=None,
    )


def test_iter_history_is_tenant_scoped_and_sorted():
    t = InMemoryWritebackTarget()
    t.write(_req("acme", "PN2", "YYZ", 5))
    t.write(_req("acme", "PN1", "YUL", 3))
    t.write(_req("acme", "PN1", "YUL", 4))  # second version for the same key
    t.write(_req("globex", "PN9", "LHR", 7))

    entries = t.iter_history("acme")
    assert [(e.pn, e.location, e.version) for e in entries] == [
        ("PN1", "YUL", 1), ("PN1", "YUL", 2), ("PN2", "YYZ", 1),
    ]
    assert all(e.tenant_id == "acme" for e in entries)


def test_iter_history_empty_tenant_is_empty_tuple():
    assert InMemoryWritebackTarget().iter_history("acme") == ()
```

- [ ] **Step 2: Run to verify failure**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv run --no-sync --extra dev --extra bff pytest tests/bvr/test_iter_history.py -q
```

Expected: FAIL — `AttributeError: 'InMemoryWritebackTarget' object has no attribute 'iter_history'`.

- [ ] **Step 3: Implement**

In `services/agent-spine/src/trax_io_spine/writeback/target.py`, add to `InMemoryWritebackTarget` (next to `get_history`):

```python
    def iter_history(self, tenant_id: str) -> tuple[HistoryEntry, ...]:
        """Every ledger entry for `tenant_id`, sorted by (pn, location, version).

        BVR input (spec §2): the report attributes over the WHOLE tenant ledger,
        not one key. In-memory-target-only by design — v1-local reports run on
        the BFF's InMemoryWritebackTarget (fake_emro is backed by this class);
        a real-eMRO enumeration API is a deferred writeback-REST concern.
        """
        entries = [
            e
            for (tid, _pn, _loc), items in self._history.items()
            if tid == tenant_id
            for e in items
        ]
        return tuple(sorted(entries, key=lambda e: (e.pn, e.location, e.version)))
```

- [ ] **Step 4: Run to green (new test + the writeback suite) + lint**

```bash
uv run --no-sync --extra dev --extra bff pytest tests/bvr/test_iter_history.py tests/writeback -q
uv run --no-sync --extra dev --extra bff ruff check .
```

Expected: all pass (2 new); ruff clean. (If the writeback tests live elsewhere, run `uv run --no-sync --extra dev --extra bff pytest -q -k writeback`.)

- [ ] **Step 5: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/src/trax_io_spine/writeback/target.py services/agent-spine/tests/bvr/test_iter_history.py
git commit -m "#8 writeback: iter_history — tenant-wide ledger enumeration for the BVR"
```

---

### Task 3: `bvr/attribution.py` — savings components (hand-computed fixtures)

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bvr/attribution.py`
- Test: `services/agent-spine/tests/bvr/test_attribution.py`

**Interfaces:**
- Consumes: `HistoryEntry`, `WritebackStatus` from `trax_io_spine.contracts`; `ProjectedComponent`, `SavingsAttribution` from Task 1.
- Produces (Task 4 imports these exact names):
  - `AttributionRates` dataclass: `holding_cost_rate=0.25`, `per_order_cost=85.0`, `stockout_proxy_fraction=0.10`, `period_fraction=1/12`, `tier_weights={1:1.0,2:0.8,3:0.6,4:0.4,5:0.2}`.
  - `KeyEconomics` dataclass: `unit_cost: float | None`, `mean_per_day: float`, `lead_mean: float`, `criticality_tier: int`.
  - `ChangeValue` dataclass: `holding: float`, `ordering: float`, `stockout: float`, `status: WritebackStatus`.
  - `value_change(old: dict[str, int], new: dict[str, int], econ: KeyEconomics, rates: AttributionRates) -> ChangeValue | None` (None ⇔ unit_cost is None — unvalued).
  - `build_savings(ledger, baseline_for, econ_for, rates) -> SavingsAttribution` where `baseline_for(entry) -> dict[str, int] | None` resolves a first write's baseline and `econ_for(pn, location) -> KeyEconomics | None`.
  - `_money(x: float) -> Decimal` (quantize helper, reused by Task 4).

- [ ] **Step 1: Write the failing tests** (hand-computed — the numbers below are the verification)

Create `services/agent-spine/tests/bvr/test_attribution.py`:

```python
"""Hand-computed fixtures for the projected-savings decomposition (spec §2).

Fixture A (single applied change), rates = defaults, period_fraction = 1/12:
  old = {rop 3, eoq 10, safety_stock 2, max_stock 20}
  new = {rop 8, eoq 20, safety_stock 4, max_stock 30}
  econ: unit_cost 100.0, mean_per_day 0.5 (annual 182.5), lead_mean 10, tier 2
  holding  = ((2+10/2) - (4+20/2)) * 100 * 0.25 / 12 = (7-14)*100*0.25/12 = -14.5833…
  ordering = (182.5/10 - 182.5/20) * 85 / 12 = 9.125*85/12 = 64.6354…
  stockout = (min(8, 5) - min(3, 5)) * 100 * 0.10 * 0.8 / 12 = 2*16/2… = 16/12 = 1.3333…
             (lead-time demand = 0.5*10 = 5 units; covered_new 5, covered_old 3)
  totals quantized: holding -14.58, ordering 64.64, stockout 1.33, sum 51.39
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trax_io_spine.bvr.attribution import (
    AttributionRates,
    KeyEconomics,
    build_savings,
    value_change,
)
from trax_io_spine.contracts import HistoryEntry, WritebackStatus

_OLD = {"rop": 3, "eoq": 10, "safety_stock": 2, "max_stock": 20}
_NEW = {"rop": 8, "eoq": 20, "safety_stock": 4, "max_stock": 30}
_ECON = KeyEconomics(unit_cost=100.0, mean_per_day=0.5, lead_mean=10.0, criticality_tier=2)
_RATES = AttributionRates()


def _entry(status: WritebackStatus, old: dict | None = _OLD) -> HistoryEntry:
    return HistoryEntry(
        tenant_id="acme", pn="PN1", location="YYZ", version=1, status=status,
        old_values=old, new_values=_NEW, provenance_id="prov-1", tier=None,
        agent_version="spine-0.1.0", changed_by_principal="agent",
        idempotency_key=None, parent_version=None,
        changed_at=datetime(2024, 4, 15, tzinfo=UTC),
    )


def test_value_change_matches_hand_computation():
    cv = value_change(_OLD, _NEW, _ECON, _RATES)
    assert cv is not None
    assert round(cv.holding, 4) == -14.5833
    assert round(cv.ordering, 4) == 64.6354
    assert round(cv.stockout, 4) == 1.3333


def test_value_change_unvalued_when_no_unit_cost():
    econ = KeyEconomics(unit_cost=None, mean_per_day=0.5, lead_mean=10.0, criticality_tier=2)
    assert value_change(_OLD, _NEW, econ, _RATES) is None


def test_value_change_skips_ordering_when_eoq_nonpositive():
    old = dict(_OLD, eoq=0)
    cv = value_change(old, _NEW, _ECON, _RATES)
    assert cv is not None
    assert cv.ordering == 0.0  # component skipped, not infinite


def test_build_savings_splits_applied_and_shadowed_and_counts_coverage():
    ledger = (
        _entry(WritebackStatus.WRITTEN),
        _entry(WritebackStatus.SHADOWED),
        _entry(WritebackStatus.FAILED),  # not WRITTEN/SHADOWED: not attributed
    )

    def baseline_for(e):  # old_values present on the fixtures
        return e.old_values

    valued = {"PN1": _ECON}

    def econ_for(pn, location):
        return valued.get(pn)

    s = build_savings(ledger, baseline_for, econ_for, _RATES)
    # per-change total = -14.5833 + 64.6354 + 1.3333 = 51.3854 -> 51.39 quantized
    assert s.total_projected_applied == Decimal("51.39")
    assert s.total_projected_shadowed == Decimal("51.39")
    assert s.total_projected == Decimal("102.77")  # quantized from 102.7708
    assert s.changes_total == 2  # WRITTEN + SHADOWED only
    assert s.changes_valued == 2
    assert s.holding_cost_delta.amount == Decimal("-29.17")  # 2 × -14.5833 = -29.1667
    assert s.ordering_cost_delta.amount == Decimal("129.27")  # 2 × 64.6354 = 129.2708
    assert s.stockout_risk_delta.amount == Decimal("2.67")  # 2 × 1.3333 = 2.6667
    assert s.assumption_rates["holding_cost_rate"] == 0.25


def test_build_savings_counts_unvalued_changes():
    ledger = (_entry(WritebackStatus.WRITTEN),)
    s = build_savings(ledger, lambda e: e.old_values, lambda pn, loc: None, _RATES)
    assert s.changes_total == 1
    assert s.changes_valued == 0
    assert s.total_projected == Decimal("0.00")


def test_build_savings_first_write_uses_baseline_for():
    ledger = (_entry(WritebackStatus.WRITTEN, old=None),)  # first agent write
    s = build_savings(ledger, lambda e: _OLD, lambda pn, loc: _ECON, _RATES)
    assert s.changes_valued == 1
    assert s.total_projected_applied == Decimal("51.39")


def test_build_savings_unresolvable_baseline_is_unvalued():
    ledger = (_entry(WritebackStatus.WRITTEN, old=None),)
    s = build_savings(ledger, lambda e: None, lambda pn, loc: _ECON, _RATES)
    assert s.changes_total == 1
    assert s.changes_valued == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv run --no-sync --extra dev --extra bff pytest tests/bvr/test_attribution.py -q
```

Expected: FAIL — `ModuleNotFoundError` / `ImportError` for `trax_io_spine.bvr.attribution`.

- [ ] **Step 3: Implement**

Create `services/agent-spine/src/trax_io_spine/bvr/attribution.py`:

```python
"""Projected-savings decomposition (spec §2) — pure, deterministic.

Baseline = the pre-agent policy (a change's `old_values`, or the extract's
CurrentPolicy for a first write — resolved by the caller via `baseline_for`).
Positive amounts = projected benefit; negatives reported as-is, never clamped.
Changes that cannot be valued (no unit cost / no baseline) are COUNTED
(`changes_total` vs `changes_valued`), never silently dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from trax_io_spine.bvr.models import ProjectedComponent, SavingsAttribution
from trax_io_spine.contracts import HistoryEntry, WritebackStatus

_ATTRIBUTED = (WritebackStatus.WRITTEN, WritebackStatus.SHADOWED)


@dataclass(frozen=True)
class AttributionRates:
    holding_cost_rate: float = 0.25  # per year
    per_order_cost: float = 85.0
    stockout_proxy_fraction: float = 0.10
    period_fraction: float = 1 / 12  # monthly-shaped report
    tier_weights: dict[int, float] = field(
        default_factory=lambda: {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.4, 5: 0.2}
    )

    def as_dict(self) -> dict[str, float]:
        return {
            "holding_cost_rate": self.holding_cost_rate,
            "per_order_cost": self.per_order_cost,
            "stockout_proxy_fraction": self.stockout_proxy_fraction,
            "period_fraction": self.period_fraction,
        }


@dataclass(frozen=True)
class KeyEconomics:
    unit_cost: float | None  # None => the change cannot be valued
    mean_per_day: float
    lead_mean: float  # days
    criticality_tier: int  # 1..5


@dataclass(frozen=True)
class ChangeValue:
    holding: float
    ordering: float
    stockout: float
    status: WritebackStatus

    @property
    def total(self) -> float:
        return self.holding + self.ordering + self.stockout


def _money(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))


def value_change(
    old: dict[str, int], new: dict[str, int], econ: KeyEconomics, rates: AttributionRates,
    *, status: WritebackStatus = WritebackStatus.WRITTEN,
) -> ChangeValue | None:
    """Value one policy change against its baseline. None ⇔ unvalued (no unit cost)."""
    if econ.unit_cost is None:
        return None
    frac = rates.period_fraction
    # Holding: Δ(safety_stock + EOQ/2) — average-position model; benefit when reduced.
    old_pos = old["safety_stock"] + old["eoq"] / 2
    new_pos = new["safety_stock"] + new["eoq"] / 2
    holding = (old_pos - new_pos) * econ.unit_cost * rates.holding_cost_rate * frac
    # Ordering frequency: annual_demand/EOQ; skipped (0.0) when either EOQ <= 0.
    annual = econ.mean_per_day * 365.0
    if old["eoq"] > 0 and new["eoq"] > 0:
        ordering = (annual / old["eoq"] - annual / new["eoq"]) * rates.per_order_cost * frac
    else:
        ordering = 0.0
    # Stockout-risk proxy: Δ units of lead-time demand covered at ROP, tier-weighted.
    ltd = econ.mean_per_day * econ.lead_mean
    covered_old = min(float(old["rop"]), ltd)
    covered_new = min(float(new["rop"]), ltd)
    weight = rates.tier_weights.get(econ.criticality_tier, 0.2)
    stockout = (
        (covered_new - covered_old)
        * econ.unit_cost * rates.stockout_proxy_fraction * weight * frac
    )
    return ChangeValue(holding=holding, ordering=ordering, stockout=stockout, status=status)


_HOLDING_FORMULA = (
    "Δ(safety_stock + EOQ/2) × unit_cost × holding_cost_rate × period_fraction"
)
_ORDERING_FORMULA = (
    "(annual_demand/EOQ_old − annual_demand/EOQ_new) × per_order_cost × period_fraction"
)
_STOCKOUT_FORMULA = (
    "Δ(lead-time demand covered at ROP) × unit_cost × stockout_proxy_fraction "
    "× tier_weight × period_fraction"
)


def build_savings(
    ledger: Iterable[HistoryEntry],
    baseline_for: Callable[[HistoryEntry], dict[str, int] | None],
    econ_for: Callable[[str, str], KeyEconomics | None],
    rates: AttributionRates,
) -> SavingsAttribution:
    holding = ordering = stockout = 0.0
    applied = shadowed = 0.0
    total = valued = 0
    for entry in ledger:
        if entry.status not in _ATTRIBUTED:
            continue
        total += 1
        baseline = entry.old_values if entry.old_values is not None else baseline_for(entry)
        econ = econ_for(entry.pn, entry.location)
        if baseline is None or econ is None:
            continue
        cv = value_change(baseline, entry.new_values, econ, rates, status=entry.status)
        if cv is None:
            continue
        valued += 1
        holding += cv.holding
        ordering += cv.ordering
        stockout += cv.stockout
        if entry.status is WritebackStatus.WRITTEN:
            applied += cv.total
        else:
            shadowed += cv.total

    assumptions = tuple(f"{k}={v}" for k, v in sorted(rates.as_dict().items()))

    def component(name: str, amount: float, formula: str) -> ProjectedComponent:
        return ProjectedComponent(
            name=name, amount=_money(amount), formula=formula,
            inputs={"changes_valued": valued, "changes_total": total},
            assumptions=assumptions,
        )

    return SavingsAttribution(
        holding_cost_delta=component("holding_cost_delta", holding, _HOLDING_FORMULA),
        ordering_cost_delta=component("ordering_cost_delta", ordering, _ORDERING_FORMULA),
        stockout_risk_delta=component("stockout_risk_delta", stockout, _STOCKOUT_FORMULA),
        total_projected_applied=_money(applied),
        total_projected_shadowed=_money(shadowed),
        total_projected=_money(applied + shadowed),
        changes_total=total,
        changes_valued=valued,
        assumption_rates=rates.as_dict(),
    )
```

- [ ] **Step 4: Run to green + lint**

```bash
uv run --no-sync --extra dev --extra bff pytest tests/bvr/test_attribution.py -q
uv run --no-sync --extra dev --extra bff ruff check .
```

Expected: 7 passed; ruff clean. If a quantized fixture value differs by one cent, re-do the hand computation before touching the code — the test numbers in Step 1's docstring are the specification.

- [ ] **Step 5: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/src/trax_io_spine/bvr/attribution.py services/agent-spine/tests/bvr/test_attribution.py
git commit -m "#8 bvr: projected-savings decomposition with hand-computed fixtures"
```

---

### Task 4: `bvr/report.py` — assembly (posture, governance, forward look, build_bvr_report)

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bvr/report.py`
- Test: `services/agent-spine/tests/bvr/test_report.py`

**Interfaces:**
- Consumes: Task 1 models; Task 3 `AttributionRates`/`KeyEconomics`/`build_savings`/`_money`; `HistoryEntry`/`WritebackStatus` from contracts; `Recommendation` from `trax_io_reco.contracts.recommendation`.
- Produces (Task 6's `PlannerStore.bvr()` and Task 5's renderer consume these):
  - `KeyFacts` dataclass: `pn: str`, `location: str`, `criticality_tier: int`, `rop: int`, `mean_per_day: float`, `lead_mean: float`, `unit_cost: float | None` — the store maps its `KeyStats` + `CurrentPolicy` into this.
  - `RecState` dataclass: `rec: Recommendation`, `status: str` (one of "pending"/"approved"/"rejected"/"deferred").
  - `build_bvr_report(*, tenant_id: str, extract_date: str | None, generated_at: datetime, key_facts: list[KeyFacts], rec_states: list[RecState], ledger: tuple[HistoryEntry, ...], baseline_for, kill_switch: bool, rates: AttributionRates | None = None, agent_version: str = "spine-0.1.0") -> BvrReport`.
  - `TIER_TARGETS = {1: 0.995, 2: 0.98, 3: 0.95, 4: 0.92, 5: 0.90}`.

- [ ] **Step 1: Write the failing tests**

Create `services/agent-spine/tests/bvr/test_report.py`:

```python
"""Assembly tests: posture, governance, forward look, determinism (spec §1, §6)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    RecommendationType,
)
from trax_io_reco.contracts.recommendation import Recommendation
from trax_io_spine.bvr.report import KeyFacts, RecState, TIER_TARGETS, build_bvr_report
from trax_io_spine.contracts import HistoryEntry, WritebackStatus

_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _rec(pn: str, impact: str, rec_id: str) -> Recommendation:
    return Recommendation(
        recommendation_id=rec_id, tenant_id="acme",
        type=RecommendationType.PURCHASE, part_number=pn, current_location="YYZ",
        current_stock=1, projected_demand=2.0, shortage_quantity=1.0,
        recommended_quantity=3.0, estimated_cost_impact=Decimal(impact),
        aog_risk_level=AogRiskLevel.LOW, criticality_tier=2, confidence_score=0.5,
        horizon_days=90, suggested_autonomy_tier=AutonomyTier.B,
        supporting_evidence=(), generated_at=_NOW, input_snapshot_hash="hashA",
        policy=None, current_policy=None,
    )


def _entry(pn: str, status: WritebackStatus, *, prov: str = "prov-1",
           tier: AutonomyTier | None = AutonomyTier.B,
           at: datetime = datetime(2024, 4, 10, tzinfo=UTC)) -> HistoryEntry:
    return HistoryEntry(
        tenant_id="acme", pn=pn, location="YYZ", version=1, status=status,
        old_values={"rop": 3, "eoq": 10, "safety_stock": 2, "max_stock": 20},
        new_values={"rop": 8, "eoq": 20, "safety_stock": 4, "max_stock": 30},
        provenance_id=prov, tier=tier, agent_version="spine-0.1.0",
        changed_by_principal="agent", idempotency_key=None, parent_version=None,
        changed_at=at,
    )


def _facts() -> list[KeyFacts]:
    return [
        # tier 1: rop 10 >= ltd 5 (0.5/day * 10d) -> at posture
        KeyFacts(pn="PN1", location="YYZ", criticality_tier=1, rop=10,
                 mean_per_day=0.5, lead_mean=10.0, unit_cost=100.0),
        # tier 1: rop 2 < ltd 5 -> below posture
        KeyFacts(pn="PN2", location="YYZ", criticality_tier=1, rop=2,
                 mean_per_day=0.5, lead_mean=10.0, unit_cost=50.0),
        # tier 3: zero demand -> ltd 0, rop 0 >= 0 -> at posture
        KeyFacts(pn="PN3", location="YUL", criticality_tier=3, rop=0,
                 mean_per_day=0.0, lead_mean=7.0, unit_cost=None),
    ]


def _states() -> list[RecState]:
    return [
        RecState(rec=_rec("PN1", "100.00", "01AAA"), status="pending"),
        RecState(rec=_rec("PN2", "250.00", "01BBB"), status="pending"),
        RecState(rec=_rec("PN3", "40.00", "01CCC"), status="approved"),
        RecState(rec=_rec("PN1", "10.00", "01DDD"), status="rejected"),
    ]


def _build(**overrides):
    kwargs = dict(
        tenant_id="acme", extract_date="2024-04-01", generated_at=_NOW,
        key_facts=_facts(), rec_states=_states(),
        ledger=(
            _entry("PN1", WritebackStatus.WRITTEN),
            _entry("PN2", WritebackStatus.SHADOWED,
                   at=datetime(2024, 4, 20, tzinfo=UTC)),
            _entry("PN3", WritebackStatus.WRITTEN, prov="rollback:prov-1", tier=None),
        ),
        baseline_for=lambda e: e.old_values,
        kill_switch=False,
    )
    kwargs.update(overrides)
    return build_bvr_report(**kwargs)


def test_service_posture_per_tier():
    r = _build()
    tiers = {t.tier: t for t in r.service_posture.tiers}
    assert set(tiers) == {1, 3}  # only tiers with keys are reported
    assert tiers[1].keys == 2 and tiers[1].keys_at_posture == 1
    assert tiers[1].posture_rate == 0.5
    assert tiers[1].target_fill_rate == TIER_TARGETS[1] == 0.995
    assert tiers[3].posture_rate == 1.0
    assert "not realized" in r.service_posture.note


def test_governance_counts_rates_and_rollbacks():
    g = _build().governance
    assert (g.recommendations_total, g.pending, g.approved, g.rejected, g.deferred) == (
        4, 2, 1, 1, 0,
    )
    assert g.approval_rate == 0.5  # 1 approved of 2 decided
    assert g.override_rate == 0.5
    assert g.writes_written == 2 and g.writes_shadowed == 1
    assert g.rollbacks == 1  # provenance_id startswith "rollback:"
    assert g.tier_mix == {"A": 0, "B": 2, "C": 0}
    assert g.kill_switch_engaged is False


def test_forward_look_ranks_pending_by_impact():
    f = _build().forward_look
    assert f.open_pipeline_value == Decimal("350.00")  # 100 + 250 (pending only)
    assert [o.pn for o in f.top_opportunities] == ["PN2", "PN1"]  # impact desc


def test_period_window_from_ledger_and_exec_summary():
    r = _build()
    assert r.period.extract_date == "2024-04-01"
    assert r.period.decision_window_start == datetime(2024, 4, 10, tzinfo=UTC)
    assert r.period.decision_window_end == datetime(2024, 4, 20, tzinfo=UTC)
    assert r.executive_summary.changes_applied == 2
    assert r.executive_summary.changes_shadowed == 1
    assert r.executive_summary.keys_under_management == 3
    assert "tiers at target posture" in r.executive_summary.service_headline
    assert r.methodology.input_snapshot_hashes == ("hashA",)


def test_report_is_deterministic_modulo_generated_at():
    a = _build().model_dump(exclude={"period": {"generated_at"}})
    b = _build().model_dump(exclude={"period": {"generated_at"}})
    assert a == b


def test_no_writes_gives_empty_window_and_zero_savings():
    r = _build(ledger=())
    assert r.period.decision_window_start is None
    assert r.savings.total_projected == Decimal("0.00")
    assert r.savings.changes_total == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv run --no-sync --extra dev --extra bff pytest tests/bvr/test_report.py -q
```

Expected: FAIL — no module `trax_io_spine.bvr.report`.

- [ ] **Step 3: Implement**

Create `services/agent-spine/src/trax_io_spine/bvr/report.py`:

```python
"""BVR assembly: posture + governance + forward look + the full report (spec §1–2).

`build_bvr_report` is pure: the BFF store maps its retained state into
`KeyFacts`/`RecState`/ledger tuples and calls this — bvr never imports bff.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trax_io_reco.contracts.recommendation import Recommendation

from trax_io_spine.bvr.attribution import (
    AttributionRates,
    KeyEconomics,
    _money,
    build_savings,
)
from trax_io_spine.bvr.models import (
    SCHEMA_VERSION,
    BvrPeriod,
    BvrReport,
    ExecutiveSummary,
    ForwardLook,
    ForwardOpportunity,
    Governance,
    Methodology,
    ServicePosture,
    TierPosture,
)
from trax_io_spine.contracts import HistoryEntry, WritebackStatus

TIER_TARGETS: dict[int, float] = {1: 0.995, 2: 0.98, 3: 0.95, 4: 0.92, 5: 0.90}

_POSTURE_NOTE = (
    "Posture (share of keys whose current ROP covers mean lead-time demand), "
    "not realized fill rate — realized service requires sequential monthly extracts."
)
_TOP_N = 10


@dataclass(frozen=True)
class KeyFacts:
    pn: str
    location: str
    criticality_tier: int  # 1..5
    rop: int
    mean_per_day: float
    lead_mean: float
    unit_cost: float | None


@dataclass(frozen=True)
class RecState:
    rec: Recommendation
    status: str  # "pending" | "approved" | "rejected" | "deferred"


def _posture(key_facts: list[KeyFacts]) -> ServicePosture:
    by_tier: dict[int, list[KeyFacts]] = {}
    for kf in key_facts:
        by_tier.setdefault(kf.criticality_tier, []).append(kf)
    tiers = []
    for tier in sorted(by_tier):
        keys = by_tier[tier]
        at = sum(1 for kf in keys if kf.rop >= kf.mean_per_day * kf.lead_mean)
        tiers.append(TierPosture(
            tier=tier, target_fill_rate=TIER_TARGETS.get(tier, 0.90),
            keys=len(keys), keys_at_posture=at,
            posture_rate=(at / len(keys)) if keys else 0.0,
        ))
    return ServicePosture(tiers=tuple(tiers), note=_POSTURE_NOTE)


def _governance(
    rec_states: list[RecState], ledger: tuple[HistoryEntry, ...], kill_switch: bool
) -> Governance:
    by_status = {"pending": 0, "approved": 0, "rejected": 0, "deferred": 0}
    for rs in rec_states:
        by_status[rs.status] = by_status.get(rs.status, 0) + 1
    decided = by_status["approved"] + by_status["rejected"]
    writes = {s: 0 for s in WritebackStatus}
    tier_mix = {"A": 0, "B": 0, "C": 0}
    rollbacks = 0
    for e in ledger:
        writes[e.status] += 1
        if e.tier is not None:
            tier_mix[e.tier.value] = tier_mix.get(e.tier.value, 0) + 1
        if e.provenance_id.startswith("rollback:"):
            rollbacks += 1
    return Governance(
        recommendations_total=len(rec_states),
        pending=by_status["pending"], approved=by_status["approved"],
        rejected=by_status["rejected"], deferred=by_status["deferred"],
        approval_rate=(by_status["approved"] / decided) if decided else 0.0,
        override_rate=(by_status["rejected"] / decided) if decided else 0.0,
        writes_written=writes[WritebackStatus.WRITTEN],
        writes_shadowed=writes[WritebackStatus.SHADOWED],
        writes_failed=writes[WritebackStatus.FAILED],
        writes_deferred_open_order=writes[WritebackStatus.DEFERRED_OPEN_ORDER],
        rollbacks=rollbacks, tier_mix=tier_mix, kill_switch_engaged=kill_switch,
    )


def _forward(rec_states: list[RecState]) -> ForwardLook:
    pending = [rs.rec for rs in rec_states if rs.status == "pending"]
    ranked = sorted(pending, key=lambda r: r.estimated_cost_impact, reverse=True)
    return ForwardLook(
        open_pipeline_value=_money(float(sum(r.estimated_cost_impact for r in pending))),
        projected_demand_horizon=sum(r.projected_demand for r in pending),
        top_opportunities=tuple(
            ForwardOpportunity(
                pn=r.part_number, location=r.current_location, type=r.type.value,
                estimated_cost_impact=r.estimated_cost_impact,
            )
            for r in ranked[:_TOP_N]
        ),
    )


def build_bvr_report(
    *, tenant_id: str, extract_date: str | None, generated_at: datetime,
    key_facts: list[KeyFacts], rec_states: list[RecState],
    ledger: tuple[HistoryEntry, ...],
    baseline_for: Callable[[HistoryEntry], dict[str, int] | None],
    kill_switch: bool, rates: AttributionRates | None = None,
    agent_version: str = "spine-0.1.0",
) -> BvrReport:
    rates = rates or AttributionRates()
    econ_by_key = {
        (kf.pn, kf.location): KeyEconomics(
            unit_cost=kf.unit_cost, mean_per_day=kf.mean_per_day,
            lead_mean=kf.lead_mean, criticality_tier=kf.criticality_tier,
        )
        for kf in key_facts
    }
    savings = build_savings(
        ledger, baseline_for, lambda pn, loc: econ_by_key.get((pn, loc)), rates
    )
    posture = _posture(key_facts)
    governance = _governance(rec_states, ledger, kill_switch)
    forward = _forward(rec_states)

    changed_ats = [e.changed_at for e in ledger]
    at_target = sum(
        1 for t in posture.tiers if t.keys and t.posture_rate >= t.target_fill_rate
    )
    headline = f"{at_target}/{len(posture.tiers)} tiers at target posture"
    hashes = tuple(sorted({rs.rec.input_snapshot_hash for rs in rec_states}))

    return BvrReport(
        schema_version=SCHEMA_VERSION,
        tenant_id=tenant_id,
        period=BvrPeriod(
            extract_date=extract_date,
            decision_window_start=min(changed_ats) if changed_ats else None,
            decision_window_end=max(changed_ats) if changed_ats else None,
            generated_at=generated_at,
            label=f"Snapshot {extract_date}" if extract_date else "Snapshot (undated)",
        ),
        executive_summary=ExecutiveSummary(
            total_projected=savings.total_projected,
            changes_applied=governance.writes_written,
            changes_shadowed=governance.writes_shadowed,
            keys_under_management=len(key_facts),
            open_pipeline_value=forward.open_pipeline_value,
            service_headline=headline,
        ),
        savings=savings,
        service_posture=posture,
        governance=governance,
        forward_look=forward,
        methodology=Methodology(
            formulas=(
                savings.holding_cost_delta.formula,
                savings.ordering_cost_delta.formula,
                savings.stockout_risk_delta.formula,
                _POSTURE_NOTE,
            ),
            assumption_rates=rates.as_dict(),
            ledger_entries=len(ledger),
            recommendations=len(rec_states),
            keys=len(key_facts),
            input_snapshot_hashes=hashes,
            agent_version=agent_version,
            generated_by="trax_io_spine.bvr",
        ),
    )
```

Note the fixture arithmetic used by `test_forward_look_ranks_pending_by_impact`: `Decimal("0.00")` when no pending recs (sum of empty ⇒ `0` int → `_money(float(0))` = `0.00`).

- [ ] **Step 4: Run bvr suite to green + lint**

```bash
uv run --no-sync --extra dev --extra bff pytest tests/bvr -q
uv run --no-sync --extra dev --extra bff ruff check .
```

Expected: all bvr tests pass (models 3 + iter_history 2 + attribution 7 + report 6); ruff clean.

- [ ] **Step 5: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/src/trax_io_spine/bvr/report.py services/agent-spine/tests/bvr/test_report.py
git commit -m "#8 bvr: report assembly — posture, governance, forward look, build_bvr_report"
```

---

### Task 5: `bvr/svg.py` + `bvr/render.py` + template — printable HTML (bvr extra)

**Files:**
- Modify: `services/agent-spine/pyproject.toml` (add extra)
- Create: `services/agent-spine/src/trax_io_spine/bvr/svg.py`, `services/agent-spine/src/trax_io_spine/bvr/render.py`, `services/agent-spine/src/trax_io_spine/bvr/templates/bvr.html.j2`
- Test: `services/agent-spine/tests/bvr/test_render.py`

**Interfaces:**
- Consumes: `BvrReport` (Task 1).
- Produces: `render_html(report: BvrReport) -> str` (Task 7 route + Task 6's pdf test use it); `svg.hbar(items: list[tuple[str, float]], *, width: int = 640) -> str` and `svg.tier_bars(tiers) -> str` (template-internal).

- [ ] **Step 1: Add the `bvr` extra**

In `services/agent-spine/pyproject.toml`, change:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.2.0", "ruff>=0.4.0"]
emro = ["fastapi>=0.111.0"]
bff = ["fastapi>=0.115"]
cedar = ["cedarpy>=4.0"]
```

to:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.2.0", "ruff>=0.4.0"]
emro = ["fastapi>=0.111.0"]
bff = ["fastapi>=0.115"]
cedar = ["cedarpy>=4.0"]
bvr = ["jinja2>=3.1"]
pdf = ["weasyprint>=61"]
```

Then sync once: `uv sync --extra dev --extra bff --extra bvr` (from `services/agent-spine`).

- [ ] **Step 2: Write the failing tests**

Create `services/agent-spine/tests/bvr/test_render.py`:

```python
"""Render smoke tests (spec §3, §6): self-contained printable HTML, inline SVG only."""

from __future__ import annotations

import pytest

jinja2 = pytest.importorskip("jinja2", reason="bvr extra not installed")

from trax_io_spine.bvr.render import render_html  # noqa: E402
from trax_io_spine.bvr.svg import hbar  # noqa: E402


def test_hbar_is_wellformed_svg_with_labels():
    out = hbar([("holding", -14.58), ("ordering", 64.64), ("stockout", 1.33)])
    assert out.startswith("<svg") and out.endswith("</svg>")
    assert 'xmlns="http://www.w3.org/2000/svg"' in out
    assert "holding" in out and "ordering" in out
    assert out.count("<rect") >= 3


def test_render_html_contains_sections_and_hero_numbers(bvr_report):
    html = render_html(bvr_report)
    for heading in (
        "Executive summary", "Savings attribution (projected)", "Service posture",
        "Governance", "Forward look", "Methodology",
    ):
        assert heading in html
    assert "51.39" in html  # total projected
    assert "projected" in html.lower()
    assert "1/1 tiers at target posture" in html
    assert "<svg" in html


def test_render_html_is_self_contained(bvr_report):
    html = render_html(bvr_report)
    assert "http://" not in html.replace("http://www.w3.org/", "")  # only the SVG xmlns
    assert "https://" not in html
    assert "<script" not in html


def test_render_html_disclosures_present(bvr_report):
    html = render_html(bvr_report)
    assert "not realized" in html  # posture note
    assert "holding_cost_rate" in html  # assumption rates disclosed
    assert "1 of 1 changes valued" in html  # coverage disclosure
```

- [ ] **Step 3: Run to verify failure**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv run --no-sync --extra dev --extra bff --extra bvr pytest tests/bvr/test_render.py -q
```

Expected: FAIL — no module `trax_io_spine.bvr.render`.

- [ ] **Step 4: Implement `svg.py`**

Create `services/agent-spine/src/trax_io_spine/bvr/svg.py`:

```python
"""Inline-SVG chart helpers — pure string builders (no JS, print-safe).

Deliberately replaces the April plan's Chart.js + headless-Chromium stack:
deterministic output, testable as strings, renders identically in WeasyPrint.
"""

from __future__ import annotations

from html import escape

_XMLNS = 'xmlns="http://www.w3.org/2000/svg"'
_POS = "#1a7f5a"  # projected benefit
_NEG = "#b3532e"  # projected cost
_BAR_H = 22
_GAP = 8
_LABEL_W = 170
_VALUE_W = 90


def hbar(items: list[tuple[str, float]], *, width: int = 640) -> str:
    """Horizontal bar chart: one row per (label, value); sign sets color + direction."""
    if not items:
        return f'<svg {_XMLNS} width="{width}" height="10"></svg>'
    scale_max = max(abs(v) for _, v in items) or 1.0
    track_w = width - _LABEL_W - _VALUE_W
    half = track_w / 2
    height = len(items) * (_BAR_H + _GAP)
    rows = []
    for i, (label, value) in enumerate(items):
        y = i * (_BAR_H + _GAP)
        w = abs(value) / scale_max * (half - 4)
        x = _LABEL_W + half - w if value < 0 else _LABEL_W + half
        color = _NEG if value < 0 else _POS
        rows.append(
            f'<text x="0" y="{y + 15}" font-size="12">{escape(label)}</text>'
            f'<rect x="{x:.1f}" y="{y + 2}" width="{max(w, 1):.1f}" '
            f'height="{_BAR_H - 6}" fill="{color}" />'
            f'<text x="{_LABEL_W + track_w + 6}" y="{y + 15}" font-size="12" '
            f'text-anchor="start">{value:,.2f}</text>'
        )
    axis = (
        f'<line x1="{_LABEL_W + half}" y1="0" x2="{_LABEL_W + half}" '
        f'y2="{height}" stroke="#999" stroke-width="1" />'
    )
    return (
        f'<svg {_XMLNS} width="{width}" height="{height}" role="img">'
        + axis + "".join(rows) + "</svg>"
    )


def tier_bars(tiers, *, width: int = 640) -> str:
    """Posture vs target per tier: filled bar = posture_rate, tick = target."""
    if not tiers:
        return f'<svg {_XMLNS} width="{width}" height="10"></svg>'
    track_w = width - _LABEL_W - _VALUE_W
    height = len(tiers) * (_BAR_H + _GAP)
    rows = []
    for i, t in enumerate(tiers):
        y = i * (_BAR_H + _GAP)
        fill_w = t.posture_rate * track_w
        tick_x = _LABEL_W + t.target_fill_rate * track_w
        rows.append(
            f'<text x="0" y="{y + 15}" font-size="12">Tier {t.tier} '
            f'({t.keys_at_posture}/{t.keys})</text>'
            f'<rect x="{_LABEL_W}" y="{y + 2}" width="{track_w}" '
            f'height="{_BAR_H - 6}" fill="#eee" />'
            f'<rect x="{_LABEL_W}" y="{y + 2}" width="{fill_w:.1f}" '
            f'height="{_BAR_H - 6}" fill="{_POS}" />'
            f'<line x1="{tick_x:.1f}" y1="{y}" x2="{tick_x:.1f}" y2="{y + _BAR_H - 2}" '
            f'stroke="#333" stroke-width="2" />'
            f'<text x="{_LABEL_W + track_w + 6}" y="{y + 15}" font-size="12">'
            f'{t.posture_rate:.0%}</text>'
        )
    return f'<svg {_XMLNS} width="{width}" height="{height}" role="img">' + "".join(rows) + "</svg>"
```

- [ ] **Step 5: Implement `render.py` + template**

Create `services/agent-spine/src/trax_io_spine/bvr/render.py`:

```python
"""Jinja2 HTML renderer for the BVR (spec §3) — one self-contained printable page."""

from __future__ import annotations

from jinja2 import Environment, PackageLoader, select_autoescape

from trax_io_spine.bvr import svg
from trax_io_spine.bvr.models import BvrReport

_env = Environment(
    loader=PackageLoader("trax_io_spine.bvr", "templates"),
    autoescape=select_autoescape(["html"]),
)


def render_html(report: BvrReport) -> str:
    savings_chart = svg.hbar([
        ("Holding cost", float(report.savings.holding_cost_delta.amount)),
        ("Ordering cost", float(report.savings.ordering_cost_delta.amount)),
        ("Stockout risk", float(report.savings.stockout_risk_delta.amount)),
    ])
    posture_chart = svg.tier_bars(report.service_posture.tiers)
    return _env.get_template("bvr.html.j2").render(
        r=report, savings_chart=savings_chart, posture_chart=posture_chart
    )
```

Create `services/agent-spine/src/trax_io_spine/bvr/templates/bvr.html.j2`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Trax IO — Business Value Report — {{ r.tenant_id }} — {{ r.period.label }}</title>
<style>
  @page { size: A4; margin: 18mm 16mm; }
  body { font: 13px/1.45 "Helvetica Neue", Arial, sans-serif; color: #1c2430; margin: 0; }
  header { border-bottom: 3px solid #0d3b66; padding-bottom: 8px; margin-bottom: 18px; }
  h1 { font-size: 21px; margin: 0; color: #0d3b66; }
  h2 { font-size: 15px; color: #0d3b66; border-bottom: 1px solid #d5dbe3;
       padding-bottom: 4px; margin: 22px 0 10px; }
  .meta { color: #5a6472; font-size: 12px; }
  .badge { display: inline-block; background: #fff3cd; border: 1px solid #d4b106;
           border-radius: 4px; padding: 1px 8px; font-size: 11px; font-weight: 700; }
  .tiles { display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0; }
  .tile { border: 1px solid #d5dbe3; border-radius: 6px; padding: 10px 14px; min-width: 150px; }
  .tile .v { font-size: 19px; font-weight: 700; }
  .tile .k { font-size: 11px; color: #5a6472; text-transform: uppercase; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; }
  th, td { border-bottom: 1px solid #e3e7ed; text-align: left; padding: 5px 8px; }
  th { color: #5a6472; text-transform: uppercase; font-size: 10px; }
  td.num, th.num { text-align: right; }
  .note { color: #5a6472; font-style: italic; font-size: 12px; }
  footer { margin-top: 26px; border-top: 1px solid #d5dbe3; padding-top: 6px;
           color: #5a6472; font-size: 10px; }
</style>
</head>
<body>
<header>
  <h1>Trax IO — Business Value Report</h1>
  <div class="meta">Tenant {{ r.tenant_id }} · {{ r.period.label }} ·
    generated {{ r.period.generated_at.isoformat() }} ·
    schema {{ r.schema_version }} <span class="badge">ALL FIGURES PROJECTED</span></div>
</header>

<h2>Executive summary</h2>
<div class="tiles">
  <div class="tile"><div class="v">${{ r.executive_summary.total_projected }}</div>
    <div class="k">Total projected value</div></div>
  <div class="tile"><div class="v">{{ r.executive_summary.changes_applied }}</div>
    <div class="k">Changes applied</div></div>
  <div class="tile"><div class="v">{{ r.executive_summary.changes_shadowed }}</div>
    <div class="k">Changes shadowed</div></div>
  <div class="tile"><div class="v">{{ "{:,}".format(r.executive_summary.keys_under_management) }}</div>
    <div class="k">Keys under management</div></div>
  <div class="tile"><div class="v">${{ r.executive_summary.open_pipeline_value }}</div>
    <div class="k">Open pipeline</div></div>
</div>
<p>{{ r.executive_summary.service_headline }}.</p>

<h2>Savings attribution (projected)</h2>
<p class="note">{{ r.savings.changes_valued }} of {{ r.savings.changes_total }} changes valued.
Applied ${{ r.savings.total_projected_applied }} + shadowed
${{ r.savings.total_projected_shadowed }} = total ${{ r.savings.total_projected }}.</p>
{{ savings_chart | safe }}
<table>
  <tr><th>Component</th><th class="num">Projected amount</th><th>Formula</th></tr>
  {% for c in (r.savings.holding_cost_delta, r.savings.ordering_cost_delta,
               r.savings.stockout_risk_delta) %}
  <tr><td>{{ c.name }}</td><td class="num">${{ c.amount }}</td><td>{{ c.formula }}</td></tr>
  {% endfor %}
</table>

<h2>Service posture</h2>
<p class="note">{{ r.service_posture.note }}</p>
{{ posture_chart | safe }}
<table>
  <tr><th>Tier</th><th class="num">Target fill rate</th><th class="num">Keys</th>
      <th class="num">At posture</th><th class="num">Posture rate</th></tr>
  {% for t in r.service_posture.tiers %}
  <tr><td>{{ t.tier }}</td><td class="num">{{ "%.1f%%" | format(t.target_fill_rate * 100) }}</td>
      <td class="num">{{ "{:,}".format(t.keys) }}</td>
      <td class="num">{{ "{:,}".format(t.keys_at_posture) }}</td>
      <td class="num">{{ "%.1f%%" | format(t.posture_rate * 100) }}</td></tr>
  {% endfor %}
</table>

<h2>Governance</h2>
<table>
  <tr><th>Recommendations</th><th class="num">Pending</th><th class="num">Approved</th>
      <th class="num">Rejected</th><th class="num">Deferred</th>
      <th class="num">Approval rate</th><th class="num">Override rate</th></tr>
  <tr><td class="num">{{ r.governance.recommendations_total }}</td>
      <td class="num">{{ r.governance.pending }}</td>
      <td class="num">{{ r.governance.approved }}</td>
      <td class="num">{{ r.governance.rejected }}</td>
      <td class="num">{{ r.governance.deferred }}</td>
      <td class="num">{{ "%.1f%%" | format(r.governance.approval_rate * 100) }}</td>
      <td class="num">{{ "%.1f%%" | format(r.governance.override_rate * 100) }}</td></tr>
</table>
<p>Writes: {{ r.governance.writes_written }} written ·
{{ r.governance.writes_shadowed }} shadowed · {{ r.governance.writes_failed }} failed ·
{{ r.governance.writes_deferred_open_order }} deferred (open order) ·
{{ r.governance.rollbacks }} rollbacks · tier mix
A {{ r.governance.tier_mix.get("A", 0) }} / B {{ r.governance.tier_mix.get("B", 0) }} /
C {{ r.governance.tier_mix.get("C", 0) }} · kill switch
{{ "ENGAGED" if r.governance.kill_switch_engaged else "off" }}.</p>

<h2>Forward look</h2>
<p>Open pipeline ${{ r.forward_look.open_pipeline_value }} ·
projected demand over horizon {{ "%.1f" | format(r.forward_look.projected_demand_horizon) }}.</p>
<table>
  <tr><th>PN</th><th>Location</th><th>Type</th><th class="num">Est. cost impact</th></tr>
  {% for o in r.forward_look.top_opportunities %}
  <tr><td>{{ o.pn }}</td><td>{{ o.location }}</td><td>{{ o.type }}</td>
      <td class="num">${{ o.estimated_cost_impact }}</td></tr>
  {% endfor %}
</table>

<h2>Methodology</h2>
<ul>
  {% for f in r.methodology.formulas %}<li>{{ f }}</li>{% endfor %}
</ul>
<p class="note">Assumption rates:
{% for k, v in r.methodology.assumption_rates.items() %}{{ k }}={{ v }}{{ ", " if not loop.last }}{% endfor %}.
Inputs: {{ r.methodology.ledger_entries }} ledger entries ·
{{ r.methodology.recommendations }} recommendations · {{ "{:,}".format(r.methodology.keys) }} keys ·
snapshot hashes {{ r.methodology.input_snapshot_hashes | join(", ") }}.
Agent {{ r.methodology.agent_version }} · generated by {{ r.methodology.generated_by }}.</p>

<footer>Trax IO Business Value Report · schema {{ r.schema_version }} ·
all monetary figures are projected against the pre-agent extract baseline.</footer>
</body>
</html>
```

- [ ] **Step 6: Run to green + full suite + lint**

```bash
uv run --no-sync --extra dev --extra bff --extra bvr pytest tests/bvr/test_render.py -q
uv run --no-sync --extra dev --extra bff --extra bvr pytest -q
uv run --no-sync --extra dev --extra bff --extra bvr ruff check .
```

Expected: render tests pass ("1 of 1 changes valued" comes from the template's coverage line — adjust the template wording, never the assertion); full suite green; ruff clean.

- [ ] **Step 7: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/pyproject.toml services/agent-spine/uv.lock services/agent-spine/src/trax_io_spine/bvr services/agent-spine/tests/bvr/test_render.py
git commit -m "#8 bvr: printable HTML renderer (jinja2 bvr extra, inline-SVG charts)"
```

---

### Task 6: `bvr/pdf.py` — WeasyPrint behind the `pdf` extra (skip-clean)

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bvr/pdf.py`
- Test: `services/agent-spine/tests/bvr/test_pdf.py`

**Interfaces:**
- Consumes: `render_html` (Task 5).
- Produces: `render_pdf(html: str) -> bytes` and `PdfUnavailable(RuntimeError)` — Task 7's `.pdf` route imports both.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/bvr/test_pdf.py`:

```python
"""Skip-gated PDF round-trip (spec §3): weasyprint is an optional extra whose native
libs (pango/cairo) may be absent — tests must skip cleanly, never fail, without them.
macOS local runs need: DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
"""

from __future__ import annotations

import pytest


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:  # ImportError OR OSError from missing native libs
        return False


pytestmark = pytest.mark.skipif(
    not _weasyprint_available(),
    reason="weasyprint (pdf extra) not installed or native pango/cairo libs not loadable",
)


def test_render_pdf_round_trip(bvr_report):
    from trax_io_spine.bvr.pdf import render_pdf
    from trax_io_spine.bvr.render import render_html

    pdf = render_pdf(render_html(bvr_report))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000
```

- [ ] **Step 2: Run to verify red (or clean skip)**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv sync --extra dev --extra bff --extra bvr --extra pdf
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run --no-sync --extra dev --extra bff --extra bvr --extra pdf pytest tests/bvr/test_pdf.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'trax_io_spine.bvr.pdf'` (the env var makes weasyprint loadable on this machine, so the test runs rather than skips). Also verify the skip path: `uv run --no-sync --extra dev --extra bff --extra bvr pytest tests/bvr/test_pdf.py -q` (without the pdf extra) → `1 skipped`.

- [ ] **Step 3: Implement**

Create `services/agent-spine/src/trax_io_spine/bvr/pdf.py`:

```python
"""WeasyPrint PDF rendering — optional `pdf` extra (spec §3).

Lazy import: the module is importable without weasyprint; `render_pdf` raises
`PdfUnavailable` with actionable detail when the extra or its native libs
(pango/cairo) are absent. macOS local: DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib.
The Docker BFF image installs the libs via apt (deploy/bff.Dockerfile).
"""

from __future__ import annotations


class PdfUnavailable(RuntimeError):
    """The pdf extra (weasyprint) or its native libraries are not installed."""


def render_pdf(html: str) -> bytes:
    try:
        import weasyprint
    except Exception as exc:  # ImportError or OSError (missing pango/cairo dylibs)
        raise PdfUnavailable(
            "PDF rendering requires the 'pdf' extra (weasyprint) and its native "
            "pango/cairo libraries — install with `uv sync --extra pdf`; on macOS "
            "also set DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib"
        ) from exc
    return weasyprint.HTML(string=html).write_pdf()
```

- [ ] **Step 4: Run to green (both paths) + lint**

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run --no-sync --extra dev --extra bff --extra bvr --extra pdf pytest tests/bvr/test_pdf.py -q
uv run --no-sync --extra dev --extra bff --extra bvr ruff check .
```

Expected: 1 passed (with env var); ruff clean.

- [ ] **Step 5: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/src/trax_io_spine/bvr/pdf.py services/agent-spine/tests/bvr/test_pdf.py services/agent-spine/uv.lock
git commit -m "#8 bvr: WeasyPrint PDF behind the pdf extra (skip-clean tests)"
```

---

### Task 7: BFF — `PlannerStore.bvr()` memoized + three `/reports/bvr*` routes

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py`
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py`
- Test: `services/agent-spine/tests/bff/test_reports.py` (new)

**Interfaces:**
- Consumes: `build_bvr_report`/`KeyFacts`/`RecState` (Task 4), `render_html` (Task 5), `render_pdf`/`PdfUnavailable` (Task 6), `iter_history` (Task 2), existing `_key_stats()`/`_safe`/`_manifest`.
- Produces: `PlannerStore.bvr() -> BvrReport` (memoized in `_bvr_cache`, invalidated by approve/reject/defer/bulk_approve/rollback); routes `GET {base}/reports/bvr`, `GET {base}/reports/bvr.html`, `GET {base}/reports/bvr.pdf` (501 when pdf unavailable). Task 8's UI calls these paths verbatim.

- [ ] **Step 1: Write the failing tests**

Create `services/agent-spine/tests/bff/test_reports.py`:

```python
"""#8 BVR: BFF reports surface — JSON/HTML/PDF routes, memoization + invalidation,
tenant isolation. Seeded from the committed sample extract like test_precompute."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.models import TaskStatus
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)
_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _store() -> PlannerStore:
    return PlannerStore.from_extract(tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW)


def test_bvr_json_shape_and_projected_labeling():
    store = _store()
    client = TestClient(create_planner_app({"acme": store}))
    body = client.get("/v1/tenants/acme/reports/bvr").json()
    assert body["schema_version"] == "1.0.0"
    assert body["tenant_id"] == "acme"
    assert body["savings"]["changes_total"] == body["governance"]["writes_written"] + (
        body["governance"]["writes_shadowed"]
    )
    assert body["executive_summary"]["keys_under_management"] == len(store.keys)
    assert body["service_posture"]["note"].startswith("Posture")


def test_bvr_html_route_serves_printable_document():
    client = TestClient(create_planner_app({"acme": _store()}))
    resp = client.get("/v1/tenants/acme/reports/bvr.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Business Value Report" in resp.text
    assert "ALL FIGURES PROJECTED" in resp.text


def test_bvr_memoized_and_invalidated_by_approve():
    store = _store()
    first = store.bvr()
    assert store.bvr() is first  # memoized
    pending = store.queue(status=TaskStatus.PENDING, limit=10)
    approvable = next(r for r in pending if r.approvable)
    store.approve(approvable.recommendation_id)
    second = store.bvr()
    assert second is not first
    assert second.governance.approved == first.governance.approved + 1
    assert second.governance.writes_written >= first.governance.writes_written


def test_bvr_pdf_route_501_when_extra_absent(monkeypatch):
    # Simulate the pdf extra being unavailable regardless of this machine's env.
    import trax_io_spine.bff.app as app_mod
    from trax_io_spine.bvr.pdf import PdfUnavailable

    def _boom(html: str) -> bytes:
        raise PdfUnavailable("pdf extra not installed")

    monkeypatch.setattr(app_mod, "render_pdf", _boom)
    client = TestClient(create_planner_app({"acme": _store()}))
    resp = client.get("/v1/tenants/acme/reports/bvr.pdf")
    assert resp.status_code == 501
    assert "pdf" in resp.json()["detail"].lower()


def test_bvr_tenant_isolation():
    client = TestClient(create_planner_app({"acme": _store()}))
    assert client.get("/v1/tenants/globex/reports/bvr").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv run --no-sync --extra dev --extra bff --extra bvr pytest tests/bff/test_reports.py -q
```

Expected: FAIL — `AttributeError: 'PlannerStore' object has no attribute 'bvr'` / 404 on the routes.

- [ ] **Step 3: Implement the store side**

In `services/agent-spine/src/trax_io_spine/bff/store.py`:

Add imports (with the existing `trax_io_spine` imports):

```python
from trax_io_spine.bvr.models import BvrReport
from trax_io_spine.bvr.report import KeyFacts, RecState, build_bvr_report
```

Add the cache field next to `_key_stats_cache` (line ~165):

```python
    _bvr_cache: BvrReport | None = field(default=None, repr=False)
```

Add the method (near `dashboard`):

```python
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
```

Invalidate in each decision path — add `self._bvr_cache = None` as the first mutation line inside `approve`, `reject`, `defer`, `bulk_approve`, and `rollback` (e.g. in `approve`, immediately after the kill-switch check).

- [ ] **Step 4: Implement the routes**

In `services/agent-spine/src/trax_io_spine/bff/app.py`:

Add imports:

```python
from fastapi import Response

from trax_io_spine.bvr.models import BvrReport
from trax_io_spine.bvr.pdf import PdfUnavailable, render_pdf
from trax_io_spine.bvr.render import render_html
```

Add the routes (near the dashboard route):

```python
    @app.get(base + "/reports/bvr")
    def bvr_json(tenant_id: str) -> BvrReport:
        return _store(tenant_id).bvr()

    @app.get(base + "/reports/bvr.html")
    def bvr_html(tenant_id: str) -> Response:
        html = render_html(_store(tenant_id).bvr())
        return Response(content=html, media_type="text/html")

    @app.get(base + "/reports/bvr.pdf")
    def bvr_pdf(tenant_id: str) -> Response:
        html = render_html(_store(tenant_id).bvr())
        try:
            pdf = render_pdf(html)
        except PdfUnavailable as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return Response(content=pdf, media_type="application/pdf")
```

Note: `.html`/`.pdf` suffixes in a path segment are literal characters — FastAPI matches them fine.

- [ ] **Step 5: Run to green + the full agent-spine suite + lint**

```bash
uv run --no-sync --extra dev --extra bff --extra bvr pytest tests/bff/test_reports.py -q
uv run --no-sync --extra dev --extra bff --extra bvr pytest -q
uv run --no-sync --extra dev --extra bff --extra bvr ruff check .
```

Expected: 5 new passing; full suite green (the bff extra tests all still pass — note `create_planner_app` now imports bvr modules, so jinja2/`bvr` extra becomes a de-facto BFF-test dependency; if any existing `--extra bff`-only invocation breaks in CI docs, update the CLAUDE.md test command in Task 9); ruff clean.

- [ ] **Step 6: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/src/trax_io_spine/bff services/agent-spine/tests/bff/test_reports.py
git commit -m "#8 BFF: /reports/bvr + .html + .pdf routes over a memoized PlannerStore.bvr()"
```

---

### Task 8: planner-ui Reports section

**Files:**
- Modify: `apps/planner-ui/src/api/types.ts`, `apps/planner-ui/src/api/client.ts`, `apps/planner-ui/src/api/sample.ts`, `apps/planner-ui/src/components/NavRail.tsx`, `apps/planner-ui/src/App.tsx`
- Create: `apps/planner-ui/src/components/ReportsView.tsx`, `apps/planner-ui/src/components/ReportsView.module.css`
- Test: `apps/planner-ui/src/components/ReportsView.test.tsx`

**Interfaces:**
- Consumes: BFF routes from Task 7 (paths verbatim: `${base}/reports/bvr`, `…/bvr.html`, `…/bvr.pdf`).
- Produces: `PlannerClient.getBvr(tenant): Promise<BvrReport>` + `bvrDocumentUrl(tenant, kind: "html" | "pdf"): string` on BOTH clients; `SAMPLE_BVR` in sample.ts; NavRail "Reports" live item; route `#/reports`.

- [ ] **Step 1: Write the failing test**

Create `apps/planner-ui/src/components/ReportsView.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ReportsView } from "./ReportsView";
import { FakePlannerClient } from "../api/client";
import { SAMPLE_SEED } from "../api/sample";

function renderReports(client: FakePlannerClient) {
  return render(
    <MemoryRouter>
      <ReportsView client={client} tenant="acme" />
    </MemoryRouter>,
  );
}

describe("ReportsView", () => {
  it("renders the projected hero tiles and the applied/shadowed split", async () => {
    renderReports(new FakePlannerClient(SAMPLE_SEED));
    expect(await screen.findByText(/total projected/i)).toBeInTheDocument();
    expect(screen.getByText(/changes applied/i)).toBeInTheDocument();
    expect(screen.getByText(/changes shadowed/i)).toBeInTheDocument();
    expect(screen.getAllByText(/projected/i).length).toBeGreaterThan(1);
  });

  it("links to the printable HTML and the PDF", async () => {
    renderReports(new FakePlannerClient(SAMPLE_SEED));
    const open = await screen.findByRole("link", { name: /open printable report/i });
    expect(open).toHaveAttribute("href", expect.stringContaining("/reports/bvr.html"));
    const pdf = screen.getByRole("link", { name: /download pdf/i });
    expect(pdf).toHaveAttribute("href", expect.stringContaining("/reports/bvr.pdf"));
  });

  it("shows governance numbers from the report", async () => {
    renderReports(new FakePlannerClient(SAMPLE_SEED));
    expect(await screen.findByText(/approval rate/i)).toBeInTheDocument();
  });

  it("handles a failed fetch without throwing", async () => {
    const client = new FakePlannerClient(SAMPLE_SEED);
    client.getBvr = async () => {
      throw new Error("boom");
    };
    renderReports(client);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t load/i);
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/apps/planner-ui"
npm test 2>&1 | tail -5
```

Expected: FAIL — cannot resolve `./ReportsView` (and `getBvr` missing on the client type).

- [ ] **Step 3: Types + clients + sample**

In `apps/planner-ui/src/api/types.ts`, append (full mirror of Task 1's models, camel-preserving the wire's snake_case like `DashboardSummary` does):

```typescript
export interface ProjectedComponent {
  name: string;
  amount: string; // Decimal serialized as string by the BFF
  formula: string;
  inputs: Record<string, number>;
  assumptions: string[];
}

export interface BvrSavings {
  holding_cost_delta: ProjectedComponent;
  ordering_cost_delta: ProjectedComponent;
  stockout_risk_delta: ProjectedComponent;
  total_projected_applied: string;
  total_projected_shadowed: string;
  total_projected: string;
  changes_total: number;
  changes_valued: number;
  assumption_rates: Record<string, number>;
}

export interface TierPosture {
  tier: number;
  target_fill_rate: number;
  keys: number;
  keys_at_posture: number;
  posture_rate: number;
}

export interface BvrGovernance {
  recommendations_total: number;
  pending: number;
  approved: number;
  rejected: number;
  deferred: number;
  approval_rate: number;
  override_rate: number;
  writes_written: number;
  writes_shadowed: number;
  writes_failed: number;
  writes_deferred_open_order: number;
  rollbacks: number;
  tier_mix: Record<string, number>;
  kill_switch_engaged: boolean;
}

export interface BvrReport {
  schema_version: string;
  tenant_id: string;
  period: {
    extract_date: string | null;
    decision_window_start: string | null;
    decision_window_end: string | null;
    generated_at: string;
    label: string;
  };
  executive_summary: {
    total_projected: string;
    changes_applied: number;
    changes_shadowed: number;
    keys_under_management: number;
    open_pipeline_value: string;
    service_headline: string;
  };
  savings: BvrSavings;
  service_posture: { tiers: TierPosture[]; note: string };
  governance: BvrGovernance;
  forward_look: {
    open_pipeline_value: string;
    projected_demand_horizon: number;
    top_opportunities: {
      pn: string;
      location: string;
      type: string;
      estimated_cost_impact: string;
    }[];
  };
  methodology: {
    formulas: string[];
    assumption_rates: Record<string, number>;
    ledger_entries: number;
    recommendations: number;
    keys: number;
    input_snapshot_hashes: string[];
    agent_version: string;
    generated_by: string;
  };
}
```

In `apps/planner-ui/src/api/client.ts`:

- `PlannerClient` interface — add:

```typescript
  getBvr(tenant: string): Promise<BvrReport>;
  bvrDocumentUrl(tenant: string, kind: "html" | "pdf"): string;
```

- `HttpPlannerClient` — add (mirroring `getDashboard`):

```typescript
  async getBvr(tenant: string): Promise<BvrReport> {
    return this.json(await fetch(`${this.base(tenant)}/reports/bvr`));
  }

  bvrDocumentUrl(tenant: string, kind: "html" | "pdf"): string {
    return `${this.base(tenant)}/reports/bvr.${kind}`;
  }
```

- `FakePlannerClient` — add:

```typescript
  async getBvr(_tenant: string): Promise<BvrReport> {
    return SAMPLE_BVR;
  }

  bvrDocumentUrl(tenant: string, kind: "html" | "pdf"): string {
    return `/v1/tenants/${tenant}/reports/bvr.${kind}`;
  }
```

In `apps/planner-ui/src/api/sample.ts`, add (and export; import `BvrReport` type):

```typescript
export const SAMPLE_BVR: BvrReport = {
  schema_version: "1.0.0",
  tenant_id: "acme",
  period: {
    extract_date: "2026-04-01",
    decision_window_start: "2026-04-01T09:00:00+00:00",
    decision_window_end: "2026-04-01T09:00:00+00:00",
    generated_at: "2026-04-01T10:00:00+00:00",
    label: "Snapshot 2026-04-01",
  },
  executive_summary: {
    total_projected: "51.39",
    changes_applied: 1,
    changes_shadowed: 0,
    keys_under_management: 4,
    open_pipeline_value: "1250.00",
    service_headline: "2/3 tiers at target posture",
  },
  savings: {
    holding_cost_delta: {
      name: "holding_cost_delta", amount: "-14.58",
      formula: "Δ(safety_stock + EOQ/2) × unit_cost × holding_cost_rate × period_fraction",
      inputs: { changes_valued: 1, changes_total: 1 },
      assumptions: ["holding_cost_rate=0.25"],
    },
    ordering_cost_delta: {
      name: "ordering_cost_delta", amount: "64.64",
      formula: "(annual_demand/EOQ_old − annual_demand/EOQ_new) × per_order_cost × period_fraction",
      inputs: { changes_valued: 1, changes_total: 1 },
      assumptions: ["per_order_cost=85.0"],
    },
    stockout_risk_delta: {
      name: "stockout_risk_delta", amount: "1.33",
      formula: "Δ(lead-time demand covered at ROP) × unit_cost × proxy × tier_weight × period_fraction",
      inputs: { changes_valued: 1, changes_total: 1 },
      assumptions: ["stockout_proxy_fraction=0.10"],
    },
    total_projected_applied: "51.39",
    total_projected_shadowed: "0.00",
    total_projected: "51.39",
    changes_total: 1,
    changes_valued: 1,
    assumption_rates: { holding_cost_rate: 0.25, per_order_cost: 85, stockout_proxy_fraction: 0.1 },
  },
  service_posture: {
    tiers: [
      { tier: 1, target_fill_rate: 0.995, keys: 1, keys_at_posture: 1, posture_rate: 1 },
      { tier: 3, target_fill_rate: 0.95, keys: 2, keys_at_posture: 1, posture_rate: 0.5 },
    ],
    note: "Posture (ROP covers mean lead-time demand), not realized fill rate.",
  },
  governance: {
    recommendations_total: 4, pending: 3, approved: 1, rejected: 0, deferred: 0,
    approval_rate: 1, override_rate: 0, writes_written: 1, writes_shadowed: 0,
    writes_failed: 0, writes_deferred_open_order: 0, rollbacks: 0,
    tier_mix: { A: 0, B: 1, C: 0 }, kill_switch_engaged: false,
  },
  forward_look: {
    open_pipeline_value: "1250.00",
    projected_demand_horizon: 18.5,
    top_opportunities: [
      { pn: "HYD-PUMP-001", location: "YYZ", type: "transfer", estimated_cost_impact: "850.00" },
    ],
  },
  methodology: {
    formulas: ["holding: Δ(ss + EOQ/2) × unit_cost × 0.25/yr × 1/12"],
    assumption_rates: { holding_cost_rate: 0.25 },
    ledger_entries: 1, recommendations: 4, keys: 4,
    input_snapshot_hashes: ["sample"], agent_version: "spine-0.1.0",
    generated_by: "trax_io_spine.bvr",
  },
};
```

- [ ] **Step 4: NavRail + route + view**

In `apps/planner-ui/src/components/NavRail.tsx`, add to `ITEMS` (import `FileText` from `lucide-react`; extend the id union with `"reports"`):

```typescript
  { id: "reports", label: "Reports", icon: FileText, live: true, href: "#/reports" },
```

In `apps/planner-ui/src/App.tsx`, add above the `/:tab` route (import `ReportsView`):

```tsx
<Route path="/reports" element={<ReportsView client={client} tenant={tenant} />} />
```

Create `apps/planner-ui/src/components/ReportsView.tsx` (mirrors `DashboardView`'s load pattern — `role="status"` while loading, `role="alert"` with "Couldn't load…" on error):

```tsx
import { useEffect, useState } from "react";
import type { PlannerClient } from "../api/client";
import type { BvrReport } from "../api/types";
import { NavRail } from "./NavRail";
import styles from "./ReportsView.module.css";
import shellStyles from "../App.module.css";

interface Props {
  client: PlannerClient;
  tenant: string;
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.tile}>
      <div className={styles.tileValue}>{value}</div>
      <div className={styles.tileLabel}>{label}</div>
    </div>
  );
}

export function ReportsView({ client, tenant }: Props) {
  const [report, setReport] = useState<BvrReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    client
      .getBvr(tenant)
      .then((r) => alive && setReport(r))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [client, tenant]);

  return (
    <div className={shellStyles.shell}>
      <NavRail active="reports" />
      <main className={styles.main}>
        <header className={styles.header}>
          <h1>Business Value Report</h1>
          {report && (
            <span className={styles.badge} title="Projected against the pre-agent baseline">
              All figures projected
            </span>
          )}
        </header>
        {error && <p role="alert">Couldn&apos;t load the report: {error}</p>}
        {!report && !error && <p role="status">Loading report…</p>}
        {report && (
          <>
            <p className={styles.meta}>
              {report.period.label} · schema {report.schema_version} ·{" "}
              {report.executive_summary.service_headline}
            </p>
            <section className={styles.tiles} aria-label="Executive summary">
              <Tile label="Total projected" value={`$${report.executive_summary.total_projected}`} />
              <Tile label="Changes applied" value={String(report.executive_summary.changes_applied)} />
              <Tile label="Changes shadowed" value={String(report.executive_summary.changes_shadowed)} />
              <Tile
                label="Keys under management"
                value={report.executive_summary.keys_under_management.toLocaleString()}
              />
              <Tile label="Open pipeline" value={`$${report.executive_summary.open_pipeline_value}`} />
            </section>
            <section aria-label="Savings decomposition" className={styles.section}>
              <h2>Savings (projected)</h2>
              <p className={styles.meta}>
                {report.savings.changes_valued} of {report.savings.changes_total} changes valued ·
                applied ${report.savings.total_projected_applied} · shadowed $
                {report.savings.total_projected_shadowed}
              </p>
              <ul className={styles.components}>
                {[
                  report.savings.holding_cost_delta,
                  report.savings.ordering_cost_delta,
                  report.savings.stockout_risk_delta,
                ].map((c) => (
                  <li key={c.name}>
                    <span className={styles.componentName}>{c.name}</span>
                    <span className={styles.componentAmount}>${c.amount}</span>
                  </li>
                ))}
              </ul>
            </section>
            <section aria-label="Governance" className={styles.section}>
              <h2>Governance</h2>
              <p>
                {report.governance.recommendations_total} recommendations · approval rate{" "}
                {(report.governance.approval_rate * 100).toFixed(1)}% · override rate{" "}
                {(report.governance.override_rate * 100).toFixed(1)}% ·{" "}
                {report.governance.rollbacks} rollbacks
              </p>
            </section>
            <p className={styles.links}>
              <a
                href={client.bvrDocumentUrl(tenant, "html")}
                target="_blank"
                rel="noreferrer"
              >
                Open printable report
              </a>
              <a href={client.bvrDocumentUrl(tenant, "pdf")}>Download PDF</a>
            </p>
          </>
        )}
      </main>
    </div>
  );
}
```

Create `apps/planner-ui/src/components/ReportsView.module.css`:

```css
.main { flex: 1; padding: 20px 28px; overflow: auto; }
.header { display: flex; align-items: center; gap: 12px; }
.header h1 { font-size: 20px; margin: 0; }
.badge { background: #fff3cd; border: 1px solid #d4b106; border-radius: 4px;
         padding: 2px 10px; font-size: 11px; font-weight: 700; }
.meta { color: #667085; font-size: 13px; }
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin: 14px 0; }
.tile { border: 1px solid #e3e7ed; border-radius: 8px; padding: 12px 16px; min-width: 150px; }
.tileValue { font-size: 20px; font-weight: 700; }
.tileLabel { font-size: 11px; color: #667085; text-transform: uppercase; }
.section { margin-top: 18px; }
.section h2 { font-size: 15px; margin: 0 0 6px; }
.components { list-style: none; padding: 0; margin: 0; max-width: 420px; }
.components li { display: flex; justify-content: space-between; padding: 4px 0;
                 border-bottom: 1px solid #eef1f5; }
.componentName { color: #344054; }
.componentAmount { font-variant-numeric: tabular-nums; }
.links { margin-top: 20px; display: flex; gap: 18px; }
```

- [ ] **Step 5: Run to green + build**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/apps/planner-ui"
npm test 2>&1 | tail -4
npm run build 2>&1 | tail -3
```

Expected: all Vitest pass (4 new; NavRail/App tests may need the new item asserted if any test snapshots the ITEMS list — fix forward, keep intent); build clean. If `NavRail` takes an `active` prop with a union type, extend it with `"reports"`; if its prop name differs, match the existing `DashboardView` usage exactly.

- [ ] **Step 6: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add apps/planner-ui/src
git commit -m "#8 planner-ui: live Reports section (#/reports) over the BVR endpoints"
```

---

### Task 9: OPS — Docker libs + extras, live verify on 58.9K, UAT + trackers (controller-run, not a subagent task)

**Files:**
- Modify: `deploy/bff.Dockerfile`, `apps/planner-ui/UAT.md`, `ROADMAP.md`, `TASKS.md`, `CLAUDE.md`

- [ ] **Step 1: Dockerfile — native libs + extras**

In `deploy/bff.Dockerfile`, after the `FROM` line add:

```dockerfile
# WeasyPrint (bvr pdf extra) native deps — pango/cairo text+render stack.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 shared-mime-info \
    && rm -rf /var/lib/apt/lists/*
```

and change the sync line to:

```dockerfile
RUN uv sync --extra bff --extra bvr --extra pdf --no-dev && uv pip install uvicorn
```

- [ ] **Step 2: Rebuild + redeploy + live verify (project `trax-io-planner` only; single sequential build)**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
docker compose build bff && docker compose build ui && docker compose up -d
curl -s http://localhost:8001/v1/tenants/acme/reports/bvr | head -c 400
curl -s -o /dev/null -w "html %{http_code}\n" http://localhost:8001/v1/tenants/acme/reports/bvr.html
curl -s http://localhost:8001/v1/tenants/acme/reports/bvr.pdf -o /tmp/bvr.pdf -w "pdf %{http_code}\n" && head -c 5 /tmp/bvr.pdf
```

Expected on the real 58.9K deploy: JSON with `schema_version 1.0.0` and `keys_under_management` ≈ 58,899; html 200; pdf 200 with `%PDF` magic (the first BVR delivered). Open http://localhost:8088 → Reports in the NavRail → tiles render, both links work. Record: report generation time on first hit (the posture scan runs over 58.9K keys — expect seconds; if >30s, note it as a follow-up, don't optimize ad hoc).

- [ ] **Step 3: UAT + trackers + commit + push**

- `apps/planner-ui/UAT.md`: new section (Reports) — cases: NavRail item live, tiles + projected badge render, printable HTML opens, PDF downloads (manual-only if the local env lacks the dylib var), error state; map to the Vitest tests; bump counts.
- `ROADMAP.md`: mark sub-project #8's first four bullets done (schema locked / attribution implemented (projected-honest) / rendering pipeline / auto-post) + a dated summary bullet; note "first BVR delivered" against the local 58.9K deploy.
- `TASKS.md`: session entry (what shipped, test counts, live numbers, honesty framing).
- `CLAUDE.md`: agent-spine test command gains `--extra bvr` (and `--extra pdf` note); BFF bullet mentions the `/reports/bvr*` routes; planner-ui bullet gains the Reports section; test counts updated to the real measured numbers.
- Commit + push:

```bash
git add deploy/bff.Dockerfile apps/planner-ui/UAT.md ROADMAP.md TASKS.md CLAUDE.md
git commit -m "#8 deploy: BVR live on the full-network portfolio (first report delivered)"
git push
```

---

## Done when

`GET /v1/tenants/acme/reports/bvr{,.html,.pdf}` serve the schema-locked, projected-only report over the real 58,899-key deploy (PDF included), the planner-ui Reports section is live at `#/reports`, all suites are green (agent-spine incl. the new bvr tests, planner-ui incl. ReportsView), UAT + trackers updated, branch pushed. Then the final whole-slice review.
