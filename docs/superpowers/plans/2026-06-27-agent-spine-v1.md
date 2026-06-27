# Agent Spine v1 (Deterministic Orchestration Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `services/agent-spine/` (`trax_io_spine`) — a deterministic Supervisor that sits downstream of #11's `RecommendationService.run()`, enforces the autonomy tier #11 only suggests, routes approvals, and writes back to eMRO (via a `fake_emro` harness), wiring the real #2 Feature Store + #11 Recommendation Engine behind Protocols.

**Architecture:** A monorepo package layering on the existing #2/#11 packages. The Supervisor binds tenant context, calls #11 to get a `RecommendationBatch`, runs each `Recommendation` through a `GuardrailEnforcer` (hard §6.2 verify + deterministic `BandAutonomyPolicy`), and writes the approved ones through a `WritebackTarget` Protocol (in-memory for tests/dry-run; httpx REST client against `fake_emro`/the real #6 endpoint). All collaborators are injected, so the Strands/AgentCore LLM topology and Cedar slot in later behind the same seams.

**Tech Stack:** Python 3.12, pydantic v2, typer (CLI), httpx (writeback client), FastAPI (fake_emro, behind an `emro` extra), `uv` + `pytest` + `ruff`. Reuses `trax-io-feature-store` and `trax-io-reco` via non-editable path sources.

Spec: [docs/superpowers/specs/2026-06-27-agent-spine-v1-design.md](../specs/2026-06-27-agent-spine-v1-design.md).

## Global Constraints

- Python `>=3.12`; `uv` for deps; src-layout; `[tool.pytest.ini_options] pythonpath = ["src"]` (mandatory — see `.claude/memory/lessons.md`).
- Cross-package deps are **non-editable** path sources: `trax-io-feature-store = { path = "../feature-store" }`, `trax-io-reco = { path = "../recommendation-engine" }`. After editing a dep, `uv sync --reinstall-package <dist-name>`.
- ruff: `line-length = 100`, `select = ["E", "F", "I", "B", "UP", "N", "SIM"]`. No mypy (matches #2/#11).
- All contracts are pydantic v2 with `model_config = ConfigDict(frozen=True, extra="forbid")`.
- Canonical tenant binding is the feature-store `TenantContext` (`from trax_io_feature_store import TenantContext`) — never redefine it.
- Reuse #11 contracts; never redefine `AutonomyTier`, `PolicyRecommendation`, `Regime`, `CanonicalCriticality`, `PolicyKind`.
- Deterministic; no LLM, no AWS, no Cedar in this slice.
- Commit after every green task.

**Key upstream APIs (consumed, not modified):**
- `from trax_io_feature_store import TenantContext, FeatureStoreClient, FeatureStoreLookupError, InMemoryFeatureStore`
- `from trax_io_reco.service import RecommendationService` — `RecommendationService(*, feature_store, inventory_state, config=None).run(*, tenant, keys, now, reporting_horizon_days=30) -> RecommendationBatch`
- `from trax_io_reco.contracts.recommendation import Recommendation, RecommendationBatch, SkippedKey` — `Recommendation` carries: `recommendation_id, tenant_id, type, part_number, current_location, current_stock, aog_risk_level: AogRiskLevel, criticality_tier: int, suggested_autonomy_tier: AutonomyTier, guardrail_flags: tuple[str,...], confidence_score: float, policy: PolicyRecommendation | None, current_policy: CurrentPolicy | None, input_snapshot_hash: str, generated_at: datetime` (+ others).
- `from trax_io_reco.contracts.enums import AutonomyTier, AogRiskLevel` — `AutonomyTier` = `ADVISOR=1, BOUNDED=2, AUTONOMOUS=3`; `AogRiskLevel` = `NONE=0, LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4`.
- `from trax_io_reco.contracts.policy import PolicyRecommendation` — `rop, eoq, safety_stock, max_stock: NonNegativeInt; policy_kind; provenance_id; tenant_id; pn; location`.
- `from trax_io_feature_store.schemas import CurrentPolicy` — `rop, eoq, safety_stock, max_stock: NonNegativeInt`.
- `from trax_io_reco.data.extract_loader import build_stores_from_extract` — `(extract_dir, *, tenant_id=None, essentiality_map=None) -> (InMemoryFeatureStore, InMemoryInventoryState, str, list[tuple[str,str]])`.

---

## File Structure

```
services/agent-spine/
├── pyproject.toml
├── README.md
├── src/trax_io_spine/
│   ├── __init__.py
│   ├── contracts.py            # GuardrailStatus, GuardrailOutcome, ApprovalTask,
│   │                           # WritebackStatus, WritebackRequest, WritebackResult,
│   │                           # OrchestrationResult (+ re-exports of #11 mirrors)
│   ├── identity.py             # tenant_scope / current_tenant (contextvar) + MissingTenantScopeError
│   ├── guardrail/
│   │   ├── __init__.py
│   │   ├── hard.py             # compute_delta_pct, hard_guardrail_violations, aog_forces_advisor
│   │   ├── policy.py           # AutonomyConfig, AutonomyPolicy Protocol, BandAutonomyPolicy
│   │   └── enforce.py          # GuardrailEnforcer.enforce(rec) -> GuardrailOutcome
│   ├── writeback/
│   │   ├── __init__.py
│   │   ├── target.py           # WritebackTarget Protocol + InMemoryWritebackTarget
│   │   ├── rest.py             # RestWritebackClient (httpx)
│   │   └── fake_emro.py        # create_fake_emro() FastAPI app (emro extra)
│   ├── supervisor.py           # Supervisor.run(...) + to_writeback_request(...)
│   └── cli.py                  # trax-io-spine CLI
└── tests/
    ├── conftest.py             # shared fixtures (tenant, a Recommendation factory)
    ├── test_contracts.py
    ├── test_identity.py
    ├── guardrail/
    │   ├── test_hard.py
    │   ├── test_policy.py
    │   └── test_enforce.py
    ├── writeback/
    │   ├── test_target.py
    │   └── test_rest.py        # uses fake_emro (emro extra)
    ├── test_supervisor.py
    └── test_integration.py     # end-to-end over #11's extract sample (emro extra)
```

---

## Phase 0: Scaffold

### Task 1: Package scaffold + dependencies

**Files:**
- Create: `services/agent-spine/pyproject.toml`
- Create: `services/agent-spine/README.md`
- Create: `services/agent-spine/src/trax_io_spine/__init__.py`
- Test: `services/agent-spine/tests/test_smoke.py`

**Interfaces:**
- Produces: an importable `trax_io_spine` package with `trax-io-feature-store` and `trax-io-reco` resolvable.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/test_smoke.py`:
```python
def test_upstream_deps_importable() -> None:
    import trax_io_spine  # noqa: F401
    from trax_io_feature_store import TenantContext  # noqa: F401
    from trax_io_reco.service import RecommendationService  # noqa: F401

    assert trax_io_spine.__version__ == "0.1.0"
```

- [ ] **Step 2: Write `pyproject.toml`**

Create `services/agent-spine/pyproject.toml`:
```toml
[project]
name = "trax-io-agent-spine"
version = "0.1.0"
description = "Trax IO Agent Spine — deterministic orchestration core (Supervisor + guardrail + writeback)"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7.0",
    "typer>=0.12.0",
    "httpx>=0.27.0",
    "trax-io-feature-store",
    "trax-io-reco",
]

[project.optional-dependencies]
dev = ["pytest>=8.2.0", "ruff>=0.4.0"]
emro = ["fastapi>=0.111.0"]

[project.scripts]
trax-io-spine = "trax_io_spine.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv.sources]
trax-io-feature-store = { path = "../feature-store" }
trax-io-reco = { path = "../recommendation-engine" }

[tool.hatch.build.targets.wheel]
packages = ["src/trax_io_spine"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "SIM"]
```

- [ ] **Step 3: Write package init + README**

Create `services/agent-spine/src/trax_io_spine/__init__.py`:
```python
"""Trax IO Agent Spine — deterministic orchestration core.

Sequences the real #2 Feature Store + #11 Recommendation Engine, enforces the autonomy
tier #11 only suggests, routes approvals, and writes back. Protocol-first so the
Strands/AgentCore LLM topology and Cedar slot in later (see
docs/superpowers/specs/2026-06-27-agent-spine-v1-design.md).
"""

__version__ = "0.1.0"
```

Create `services/agent-spine/README.md`:
```markdown
# Trax IO Agent Spine — service

Deterministic orchestration core for sub-project #4. Wires the real Feature Store (#2)
and Recommendation Engine (#11) into an enforced, written-or-queued outcome.

## Dev setup
```bash
cd services/agent-spine
uv sync --extra dev --extra emro
uv run pytest
uv run ruff check .
```

The `emro` extra pulls FastAPI for the `fake_emro` writeback harness used by the
writeback + integration tests. Core tests run without it.
```

- [ ] **Step 4: Sync and run the smoke test**

Run:
```bash
cd services/agent-spine && uv sync --extra dev --extra emro && uv run --extra dev pytest tests/test_smoke.py -q
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/
git commit -m "#4 agent-spine: package scaffold + non-editable path deps on #2/#11"
```

---

## Phase 1: Contracts

### Task 2: Spine contracts

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/contracts.py`
- Test: `services/agent-spine/tests/test_contracts.py`

**Interfaces:**
- Produces:
  - `GuardrailStatus(StrEnum)`: `APPROVED_FOR_WRITE="approved_for_write"`, `QUEUED_FOR_APPROVAL="queued_for_approval"`, `REJECTED_HARD_GUARDRAIL="rejected_hard_guardrail"`.
  - `WritebackStatus(StrEnum)`: `WRITTEN="written"`, `DEFERRED_OPEN_ORDER="deferred_open_order"`, `FAILED="failed"`.
  - `ApprovalTask(frozen)`: `task_id: str, tenant_id: str, pn: str, location: str, tier: AutonomyTier, priority_score: float (ge=0), reason: str = ""`.
  - `GuardrailOutcome(frozen)`: `recommendation_id: str, status: GuardrailStatus, tier: AutonomyTier, delta_pct: float (ge=0), reasons: tuple[str, ...] = (), approval_task: ApprovalTask | None = None`.
  - `WritebackRequest(frozen)`: `tenant_id, pn, location: str, rop, eoq, safety_stock, max_stock: NonNegativeInt, provenance_id: str, idempotency_key: str (min_length=1)`.
  - `WritebackResult(frozen)`: `tenant_id, pn, location: str, status: WritebackStatus, old_values: dict[str,int] | None = None, new_values: dict[str,int] | None = None, written_at: datetime | None = None, error_message: str | None = None`.
  - `OrchestrationResult(frozen)`: `tenant_id: str, generated_at: datetime, written, deferred, failed: tuple[WritebackResult, ...] = (), queued: tuple[ApprovalTask, ...] = (), rejected: tuple[GuardrailOutcome, ...] = (), skipped: tuple[SkippedKey, ...] = (), summary: dict[str, int] = {}`.
  - Re-exports: `AutonomyTier`, `PolicyRecommendation`, `SkippedKey`.

> Design note: the spec listed a 4-value `GuardrailStatus` incl. `deferred`; deferral is realized as a `WritebackStatus` (the eMRO endpoint reports the open-order conflict), so the enforcer emits 3 statuses and the writeback adds `deferred_open_order`. The `OrchestrationResult` buckets keep them distinct.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/test_contracts.py`:
```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trax_io_spine.contracts import (
    ApprovalTask,
    AutonomyTier,
    GuardrailOutcome,
    GuardrailStatus,
    OrchestrationResult,
    WritebackRequest,
    WritebackResult,
    WritebackStatus,
)


def test_guardrail_outcome_round_trips_json() -> None:
    out = GuardrailOutcome(
        recommendation_id="r-1",
        status=GuardrailStatus.APPROVED_FOR_WRITE,
        tier=AutonomyTier.AUTONOMOUS,
        delta_pct=0.18,
    )
    assert GuardrailOutcome.model_validate_json(out.model_dump_json()) == out


def test_approval_task_rejects_negative_priority() -> None:
    with pytest.raises(ValidationError):
        ApprovalTask(
            task_id="t-1", tenant_id="acme", pn="PN-A", location="LOC-1",
            tier=AutonomyTier.ADVISOR, priority_score=-1.0,
        )


def test_writeback_request_requires_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        WritebackRequest(
            tenant_id="acme", pn="PN-A", location="LOC-1",
            rop=5, eoq=4, safety_stock=2, max_stock=9, provenance_id="p-1",
            idempotency_key="",
        )


def test_orchestration_result_defaults_are_empty_tuples() -> None:
    res = OrchestrationResult(tenant_id="acme", generated_at=datetime.now(UTC))
    assert res.written == () and res.queued == () and res.rejected == ()


def test_writeback_result_carries_deferred_status() -> None:
    r = WritebackResult(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        status=WritebackStatus.DEFERRED_OPEN_ORDER,
    )
    assert r.status is WritebackStatus.DEFERRED_OPEN_ORDER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/test_contracts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'trax_io_spine.contracts'`.

- [ ] **Step 3: Implement `contracts.py`**

Create `services/agent-spine/src/trax_io_spine/contracts.py`:
```python
"""Agent Spine contracts: guardrail outcomes, writeback I/O, orchestration result.

Re-exports #11's mirrors (AutonomyTier, PolicyRecommendation) and #11's SkippedKey so the
spine consumes the engine's types verbatim rather than redefining them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt
from trax_io_reco.contracts.enums import AutonomyTier
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import SkippedKey

__all__ = [
    "ApprovalTask",
    "AutonomyTier",
    "GuardrailOutcome",
    "GuardrailStatus",
    "OrchestrationResult",
    "PolicyRecommendation",
    "SkippedKey",
    "WritebackRequest",
    "WritebackResult",
    "WritebackStatus",
]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GuardrailStatus(StrEnum):
    APPROVED_FOR_WRITE = "approved_for_write"
    QUEUED_FOR_APPROVAL = "queued_for_approval"
    REJECTED_HARD_GUARDRAIL = "rejected_hard_guardrail"


class WritebackStatus(StrEnum):
    WRITTEN = "written"
    DEFERRED_OPEN_ORDER = "deferred_open_order"
    FAILED = "failed"


class ApprovalTask(_Base):
    task_id: str
    tenant_id: str
    pn: str
    location: str
    tier: AutonomyTier
    priority_score: float = Field(ge=0.0)
    reason: str = ""


class GuardrailOutcome(_Base):
    recommendation_id: str
    status: GuardrailStatus
    tier: AutonomyTier
    delta_pct: float = Field(ge=0.0)
    reasons: tuple[str, ...] = ()
    approval_task: ApprovalTask | None = None


class WritebackRequest(_Base):
    tenant_id: str
    pn: str
    location: str
    rop: NonNegativeInt
    eoq: NonNegativeInt
    safety_stock: NonNegativeInt
    max_stock: NonNegativeInt
    provenance_id: str
    idempotency_key: str = Field(min_length=1)


class WritebackResult(_Base):
    tenant_id: str
    pn: str
    location: str
    status: WritebackStatus
    old_values: dict[str, int] | None = None
    new_values: dict[str, int] | None = None
    written_at: datetime | None = None
    error_message: str | None = None


class OrchestrationResult(_Base):
    tenant_id: str
    generated_at: datetime
    written: tuple[WritebackResult, ...] = ()
    deferred: tuple[WritebackResult, ...] = ()
    failed: tuple[WritebackResult, ...] = ()
    queued: tuple[ApprovalTask, ...] = ()
    rejected: tuple[GuardrailOutcome, ...] = ()
    skipped: tuple[SkippedKey, ...] = ()
    summary: dict[str, int] = Field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/test_contracts.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/contracts.py services/agent-spine/tests/test_contracts.py
git commit -m "#4 agent-spine: contracts (guardrail/writeback/orchestration) + #11 re-exports"
```

---

## Phase 2: Identity

### Task 3: Tenant scope (contextvar)

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/identity.py`
- Test: `services/agent-spine/tests/test_identity.py`

**Interfaces:**
- Produces: `tenant_scope(ctx: TenantContext) -> Iterator[TenantContext]` (context manager), `current_tenant() -> TenantContext`, `MissingTenantScopeError(RuntimeError)`.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/test_identity.py`:
```python
import pytest
from trax_io_feature_store import TenantContext

from trax_io_spine.identity import MissingTenantScopeError, current_tenant, tenant_scope


def test_current_tenant_raises_outside_scope() -> None:
    with pytest.raises(MissingTenantScopeError):
        current_tenant()


def test_tenant_scope_sets_and_clears() -> None:
    ctx = TenantContext(tenant_id="acme")
    with tenant_scope(ctx):
        assert current_tenant() == ctx
    with pytest.raises(MissingTenantScopeError):
        current_tenant()


def test_nested_scopes_restore_outer() -> None:
    outer = TenantContext(tenant_id="acme")
    inner = TenantContext(tenant_id="other")
    with tenant_scope(outer):
        with tenant_scope(inner):
            assert current_tenant().tenant_id == "other"
        assert current_tenant().tenant_id == "acme"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/test_identity.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `identity.py`**

Create `services/agent-spine/src/trax_io_spine/identity.py`:
```python
"""Tenant context propagation via contextvars (task-local, async-safe).

The spine binds the canonical feature-store ``TenantContext`` for the duration of an
orchestration so every step runs under one tenant. Reading outside a scope raises.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from trax_io_feature_store import TenantContext


class MissingTenantScopeError(RuntimeError):
    """Raised when tenant context is read outside any ``tenant_scope``."""


_current: ContextVar[TenantContext | None] = ContextVar("trax_io_spine_tenant", default=None)


def current_tenant() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise MissingTenantScopeError(
            "no tenant bound; wrap the call site in `with tenant_scope(...)`"
        )
    return ctx


@contextmanager
def tenant_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/test_identity.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/identity.py services/agent-spine/tests/test_identity.py
git commit -m "#4 agent-spine: tenant_scope contextvar chokepoint"
```

---

## Phase 3: Guardrail enforcement

### Task 4: Hard guardrails (§6.2 verify)

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/guardrail/__init__.py` (empty)
- Create: `services/agent-spine/src/trax_io_spine/guardrail/hard.py`
- Test: `services/agent-spine/tests/guardrail/test_hard.py`
- Create: `services/agent-spine/tests/conftest.py`

**Interfaces:**
- Produces:
  - `compute_delta_pct(policy: PolicyRecommendation, current: CurrentPolicy | None) -> float` — max relative change across (rop, eoq, safety_stock, max_stock) vs `current`; `0.0` when `current` is None or all-zero (a first-time seed has no baseline to delta against).
  - `hard_guardrail_violations(rec: Recommendation, *, delta_pct: float) -> tuple[str, ...]` — `("delta_exceeds_100pct",)` when `delta_pct > 1.0`, else `()`.
  - `aog_forces_advisor(rec: Recommendation) -> bool` — `rec.aog_risk_level >= AogRiskLevel.HIGH`.
- Consumes: `Recommendation`, `PolicyRecommendation`, `CurrentPolicy`, `AogRiskLevel`.

- [ ] **Step 1: Write the shared conftest (Recommendation factory)**

Create `services/agent-spine/tests/conftest.py`:
```python
"""Shared fixtures: a tenant + a Recommendation factory with sensible defaults."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from trax_io_feature_store import TenantContext
from trax_io_feature_store.schemas import CurrentPolicy
from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    PolicyKind,
    RecommendationType,
)
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import Recommendation


@pytest.fixture
def tenant() -> TenantContext:
    return TenantContext(tenant_id="acme")


def make_policy(**over: object) -> PolicyRecommendation:
    base: dict[str, object] = dict(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        rop=10, eoq=5, safety_stock=4, max_stock=20,
        policy_kind=PolicyKind.S_S, provenance_id="prov-1", model_id="deterministic-v1",
    )
    base.update(over)
    return PolicyRecommendation(**base)  # type: ignore[arg-type]


def make_current(**over: object) -> CurrentPolicy:
    from datetime import date

    base: dict[str, object] = dict(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        rop=10, eoq=5, safety_stock=4, max_stock=20,
        replenishment_lead_days=21.0, extract_date=date(2026, 4, 1),
    )
    base.update(over)
    return CurrentPolicy(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_rec():
    def _make(**over: object) -> Recommendation:
        policy = over.pop("policy", make_policy())
        current = over.pop("current_policy", make_current())
        base: dict[str, object] = dict(
            recommendation_id="r-1", tenant_id="acme", type=RecommendationType.ADJUST_MIN_MAX,
            part_number="PN-A", description="widget", current_location="LOC-1",
            current_stock=12, projected_demand=3.0, shortage_quantity=0.0,
            recommended_quantity=0.0, estimated_cost_impact=0, aog_risk_level=AogRiskLevel.NONE,
            criticality_tier=4, reason="test", supporting_evidence=(),
            confidence_score=0.8, horizon_days=30, suggested_autonomy_tier=AutonomyTier.AUTONOMOUS,
            guardrail_flags=(), generated_at=datetime.now(UTC), input_snapshot_hash="hash",
            policy=policy, current_policy=current,
        )
        base.update(over)
        return Recommendation(**base)  # type: ignore[arg-type]

    return _make
```

- [ ] **Step 2: Write the failing test**

Create `services/agent-spine/tests/guardrail/__init__.py` (empty) and `services/agent-spine/tests/guardrail/test_hard.py`:
```python
from trax_io_reco.contracts.enums import AogRiskLevel

from tests.conftest import make_current, make_policy
from trax_io_spine.guardrail.hard import (
    aog_forces_advisor,
    compute_delta_pct,
    hard_guardrail_violations,
)


def test_delta_pct_zero_when_no_current() -> None:
    assert compute_delta_pct(make_policy(), None) == 0.0


def test_delta_pct_is_max_relative_change() -> None:
    policy = make_policy(rop=10, eoq=5, safety_stock=4, max_stock=30)  # max 20 -> 30 = +50%
    delta = compute_delta_pct(policy, make_current(max_stock=20))
    assert delta == 0.5


def test_violation_when_delta_exceeds_100pct(make_rec) -> None:
    rec = make_rec()
    assert hard_guardrail_violations(rec, delta_pct=1.5) == ("delta_exceeds_100pct",)
    assert hard_guardrail_violations(rec, delta_pct=0.9) == ()


def test_aog_high_forces_advisor(make_rec) -> None:
    assert aog_forces_advisor(make_rec(aog_risk_level=AogRiskLevel.HIGH)) is True
    assert aog_forces_advisor(make_rec(aog_risk_level=AogRiskLevel.LOW)) is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/guardrail/test_hard.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `hard.py`**

Create `services/agent-spine/src/trax_io_spine/guardrail/__init__.py` (empty), then `services/agent-spine/src/trax_io_spine/guardrail/hard.py`:
```python
"""Non-bypassable §6.2 hard-guardrail verifiers (defense-in-depth over #11's clamps).

The engine already clamps; the spine re-derives the headline single-write delta and the
AOG-forces-Tier-A rule from the Recommendation itself, so the two layers cannot silently
diverge. Shelf-life/hazmat/tool clamps require part_attributes the spine does not re-fetch
in v1; those arrive on ``rec.guardrail_flags`` and are surfaced (not re-verified) downstream.
"""

from __future__ import annotations

from trax_io_feature_store.schemas import CurrentPolicy
from trax_io_reco.contracts.enums import AogRiskLevel
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import Recommendation

_FIELDS = ("rop", "eoq", "safety_stock", "max_stock")


def compute_delta_pct(policy: PolicyRecommendation, current: CurrentPolicy | None) -> float:
    """Max relative change across the four policy values vs the current policy.

    Returns 0.0 when there is no current policy (first-time seed: no baseline to delta against).
    """
    if current is None:
        return 0.0
    deltas: list[float] = []
    for f in _FIELDS:
        old = getattr(current, f)
        new = getattr(policy, f)
        if old == 0:
            if new != 0:
                deltas.append(1.0)  # 0 -> nonzero: treat as a full-band (100%) change
            continue
        deltas.append(abs(new - old) / old)
    return max(deltas) if deltas else 0.0


def hard_guardrail_violations(rec: Recommendation, *, delta_pct: float) -> tuple[str, ...]:
    """Reasons a recommendation must be rejected outright. Empty tuple = passes."""
    violations: list[str] = []
    if delta_pct > 1.0:
        violations.append("delta_exceeds_100pct")
    return tuple(violations)


def aog_forces_advisor(rec: Recommendation) -> bool:
    """An active AOG signal forces the most conservative tier (human approval)."""
    return rec.aog_risk_level >= AogRiskLevel.HIGH
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/guardrail/test_hard.py -q`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/guardrail/ services/agent-spine/tests/conftest.py services/agent-spine/tests/guardrail/
git commit -m "#4 agent-spine: hard §6.2 guardrails (delta cap, AOG-forces-advisor) + test factory"
```

---

### Task 5: Band autonomy policy

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/guardrail/policy.py`
- Test: `services/agent-spine/tests/guardrail/test_policy.py`

**Interfaces:**
- Produces:
  - `AutonomyConfig(frozen)`: `bounded_max_delta_pct: float = 0.25`, `autonomous_max_delta_pct: float = 1.0`, `min_autonomous_criticality_tier: int = 4`.
  - `AutonomyPolicy` Protocol: `authorize(self, *, tier: AutonomyTier, delta_pct: float, criticality_tier: int) -> GuardrailStatus`.
  - `BandAutonomyPolicy(config: AutonomyConfig | None = None)` implementing the Protocol.
- Consumes: `AutonomyTier`, `GuardrailStatus`.

**Band rules (deterministic):** `ADVISOR` always queues. `BOUNDED`/`AUTONOMOUS` auto-write iff `criticality_tier >= min_autonomous_criticality_tier` AND `delta_pct <= (bounded|autonomous)_max_delta_pct`; else queue. (`criticality_tier` is 1=most-critical..5=least, so `>= 4` means only routine/consumable parts auto-write.)

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/guardrail/test_policy.py`:
```python
from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import GuardrailStatus
from trax_io_spine.guardrail.policy import AutonomyConfig, BandAutonomyPolicy


def test_advisor_always_queues() -> None:
    p = BandAutonomyPolicy()
    assert p.authorize(tier=AutonomyTier.ADVISOR, delta_pct=0.0, criticality_tier=5) == (
        GuardrailStatus.QUEUED_FOR_APPROVAL
    )


def test_autonomous_within_band_and_low_criticality_approves() -> None:
    p = BandAutonomyPolicy()
    assert p.authorize(tier=AutonomyTier.AUTONOMOUS, delta_pct=0.5, criticality_tier=4) == (
        GuardrailStatus.APPROVED_FOR_WRITE
    )


def test_autonomous_critical_part_queues() -> None:
    p = BandAutonomyPolicy()  # criticality 3 < min 4
    assert p.authorize(tier=AutonomyTier.AUTONOMOUS, delta_pct=0.1, criticality_tier=3) == (
        GuardrailStatus.QUEUED_FOR_APPROVAL
    )


def test_bounded_band_is_tighter_than_autonomous() -> None:
    p = BandAutonomyPolicy(AutonomyConfig(bounded_max_delta_pct=0.25, autonomous_max_delta_pct=1.0))
    # delta 0.4 is inside autonomous band but outside bounded band
    assert p.authorize(tier=AutonomyTier.BOUNDED, delta_pct=0.4, criticality_tier=5) == (
        GuardrailStatus.QUEUED_FOR_APPROVAL
    )
    assert p.authorize(tier=AutonomyTier.AUTONOMOUS, delta_pct=0.4, criticality_tier=5) == (
        GuardrailStatus.APPROVED_FOR_WRITE
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/guardrail/test_policy.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `policy.py`**

Create `services/agent-spine/src/trax_io_spine/guardrail/policy.py`:
```python
"""Deterministic autonomy band policy.

Decides whether a recommendation auto-writes or queues for approval, from its effective
tier + single-write delta + part criticality. A Protocol so Cedar backs the same seam in
production (the deployment slice) without changing the enforcer.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict
from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import GuardrailStatus


class AutonomyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bounded_max_delta_pct: float = 0.25
    autonomous_max_delta_pct: float = 1.0
    min_autonomous_criticality_tier: int = 4  # 1=most critical..5=least; only >= this auto-writes


class AutonomyPolicy(Protocol):
    def authorize(
        self, *, tier: AutonomyTier, delta_pct: float, criticality_tier: int
    ) -> GuardrailStatus: ...


class BandAutonomyPolicy:
    def __init__(self, config: AutonomyConfig | None = None) -> None:
        self._cfg = config or AutonomyConfig()

    def authorize(
        self, *, tier: AutonomyTier, delta_pct: float, criticality_tier: int
    ) -> GuardrailStatus:
        if tier is AutonomyTier.ADVISOR:
            return GuardrailStatus.QUEUED_FOR_APPROVAL
        if criticality_tier < self._cfg.min_autonomous_criticality_tier:
            return GuardrailStatus.QUEUED_FOR_APPROVAL
        ceiling = (
            self._cfg.autonomous_max_delta_pct
            if tier is AutonomyTier.AUTONOMOUS
            else self._cfg.bounded_max_delta_pct
        )
        if delta_pct <= ceiling:
            return GuardrailStatus.APPROVED_FOR_WRITE
        return GuardrailStatus.QUEUED_FOR_APPROVAL
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/guardrail/test_policy.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/guardrail/policy.py services/agent-spine/tests/guardrail/test_policy.py
git commit -m "#4 agent-spine: deterministic BandAutonomyPolicy (Cedar-swappable)"
```

---

### Task 6: Guardrail enforcer

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/guardrail/enforce.py`
- Test: `services/agent-spine/tests/guardrail/test_enforce.py`

**Interfaces:**
- Produces: `GuardrailEnforcer(policy: AutonomyPolicy | None = None)` with `enforce(self, rec: Recommendation) -> GuardrailOutcome`.
- Behavior: compute `delta_pct`; if hard violations → `REJECTED_HARD_GUARDRAIL` (reasons set, no approval task); else effective tier = `ADVISOR` if `aog_forces_advisor` else `rec.suggested_autonomy_tier`; status = `policy.authorize(...)`; if `QUEUED` build an `ApprovalTask` (priority = `criticality weighting + aog + (1-confidence)`); if `rec.policy is None` → `QUEUED` with reason `non_policy_recommendation` (nothing to write).
- Consumes: `BandAutonomyPolicy`, `hard.*`, `GuardrailOutcome`, `ApprovalTask`.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/guardrail/test_enforce.py`:
```python
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier

from trax_io_spine.contracts import GuardrailStatus
from trax_io_spine.guardrail.enforce import GuardrailEnforcer

from tests.conftest import make_current, make_policy


def test_approves_autonomous_low_criticality_in_band(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=4,
        policy=make_policy(max_stock=23), current_policy=make_current(max_stock=20),  # +15%
    )
    out = GuardrailEnforcer().enforce(rec)
    assert out.status is GuardrailStatus.APPROVED_FOR_WRITE
    assert out.approval_task is None


def test_rejects_when_delta_exceeds_cap(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=5,
        policy=make_policy(rop=10, safety_stock=4, eoq=5, max_stock=60),  # 20 -> 60 = +200%
        current_policy=make_current(max_stock=20),
    )
    out = GuardrailEnforcer().enforce(rec)
    assert out.status is GuardrailStatus.REJECTED_HARD_GUARDRAIL
    assert "delta_exceeds_100pct" in out.reasons


def test_aog_forces_queue_even_when_in_band(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=4,
        aog_risk_level=AogRiskLevel.CRITICAL,
        policy=make_policy(max_stock=21), current_policy=make_current(max_stock=20),
    )
    out = GuardrailEnforcer().enforce(rec)
    assert out.status is GuardrailStatus.QUEUED_FOR_APPROVAL
    assert out.tier is AutonomyTier.ADVISOR
    assert out.approval_task is not None


def test_non_policy_recommendation_queues(make_rec) -> None:
    rec = make_rec(policy=None, current_policy=None)
    out = GuardrailEnforcer().enforce(rec)
    assert out.status is GuardrailStatus.QUEUED_FOR_APPROVAL
    assert "non_policy_recommendation" in out.reasons
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/guardrail/test_enforce.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `enforce.py`**

Create `services/agent-spine/src/trax_io_spine/guardrail/enforce.py`:
```python
"""Compose hard guardrails + band policy into a per-recommendation GuardrailOutcome."""

from __future__ import annotations

from trax_io_reco.contracts.enums import AutonomyTier
from trax_io_reco.contracts.recommendation import Recommendation

from trax_io_spine.contracts import ApprovalTask, GuardrailOutcome, GuardrailStatus
from trax_io_spine.guardrail.hard import (
    aog_forces_advisor,
    compute_delta_pct,
    hard_guardrail_violations,
)
from trax_io_spine.guardrail.policy import AutonomyPolicy, BandAutonomyPolicy


class GuardrailEnforcer:
    def __init__(self, policy: AutonomyPolicy | None = None) -> None:
        self._policy: AutonomyPolicy = policy or BandAutonomyPolicy()

    def enforce(self, rec: Recommendation) -> GuardrailOutcome:
        # Non-policy recommendations (e.g. Sell/Transfer with no ROP/EOQ change) are never
        # auto-written; a planner handles them.
        if rec.policy is None:
            return GuardrailOutcome(
                recommendation_id=rec.recommendation_id,
                status=GuardrailStatus.QUEUED_FOR_APPROVAL,
                tier=rec.suggested_autonomy_tier,
                delta_pct=0.0,
                reasons=("non_policy_recommendation",) + rec.guardrail_flags,
                approval_task=self._task(rec, rec.suggested_autonomy_tier, "non_policy"),
            )

        delta_pct = compute_delta_pct(rec.policy, rec.current_policy)
        violations = hard_guardrail_violations(rec, delta_pct=delta_pct)
        if violations:
            return GuardrailOutcome(
                recommendation_id=rec.recommendation_id,
                status=GuardrailStatus.REJECTED_HARD_GUARDRAIL,
                tier=AutonomyTier.ADVISOR,
                delta_pct=delta_pct,
                reasons=violations + rec.guardrail_flags,
            )

        tier = AutonomyTier.ADVISOR if aog_forces_advisor(rec) else rec.suggested_autonomy_tier
        status = self._policy.authorize(
            tier=tier, delta_pct=delta_pct, criticality_tier=rec.criticality_tier
        )
        task = (
            self._task(rec, tier, "band")
            if status is GuardrailStatus.QUEUED_FOR_APPROVAL
            else None
        )
        return GuardrailOutcome(
            recommendation_id=rec.recommendation_id,
            status=status,
            tier=tier,
            delta_pct=delta_pct,
            reasons=rec.guardrail_flags,
            approval_task=task,
        )

    @staticmethod
    def _task(rec: Recommendation, tier: AutonomyTier, reason: str) -> ApprovalTask:
        # Higher = more urgent: critical parts (low tier number) + AOG + low confidence.
        priority = (
            (6 - rec.criticality_tier) * 10.0
            + float(rec.aog_risk_level) * 5.0
            + (1.0 - rec.confidence_score) * 2.0
        )
        return ApprovalTask(
            task_id=f"{rec.tenant_id}:{rec.recommendation_id}",
            tenant_id=rec.tenant_id,
            pn=rec.part_number,
            location=rec.current_location,
            tier=tier,
            priority_score=priority,
            reason=reason,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/guardrail/test_enforce.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/guardrail/enforce.py services/agent-spine/tests/guardrail/test_enforce.py
git commit -m "#4 agent-spine: GuardrailEnforcer (hard-verify + band-authorize + approval task)"
```

---

## Phase 4: Writeback

### Task 7: WritebackTarget Protocol + in-memory target

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/writeback/__init__.py` (empty)
- Create: `services/agent-spine/src/trax_io_spine/writeback/target.py`
- Test: `services/agent-spine/tests/writeback/test_target.py`

**Interfaces:**
- Produces:
  - `WritebackTarget` Protocol: `write(self, req: WritebackRequest) -> WritebackResult`.
  - `InMemoryWritebackTarget(open_orders: set[tuple[str, str, str]] | None = None)`: dict-backed, idempotent by `req.idempotency_key`; a `(tenant, pn, location)` in `open_orders` → `DEFERRED_OPEN_ORDER`; else `WRITTEN` with `old_values`/`new_values`.
- Consumes: `WritebackRequest`, `WritebackResult`, `WritebackStatus`.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/writeback/__init__.py` (empty) and `services/agent-spine/tests/writeback/test_target.py`:
```python
from trax_io_spine.contracts import WritebackRequest, WritebackStatus
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(**over: object) -> WritebackRequest:
    base: dict[str, object] = dict(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        rop=5, eoq=4, safety_stock=2, max_stock=12, provenance_id="p-1",
        idempotency_key="2026-04-01:acme:PN-A:LOC-1",
    )
    base.update(over)
    return WritebackRequest(**base)  # type: ignore[arg-type]


def test_write_persists_and_returns_new_values() -> None:
    t = InMemoryWritebackTarget()
    res = t.write(_req())
    assert res.status is WritebackStatus.WRITTEN
    assert res.new_values == {"rop": 5, "eoq": 4, "safety_stock": 2, "max_stock": 12}


def test_idempotent_replay_returns_same_result_once() -> None:
    t = InMemoryWritebackTarget()
    first = t.write(_req())
    second = t.write(_req(rop=999))  # same idempotency_key -> ignored
    assert second == first
    assert len(t.history) == 1


def test_open_order_defers() -> None:
    t = InMemoryWritebackTarget(open_orders={("acme", "PN-A", "LOC-1")})
    res = t.write(_req())
    assert res.status is WritebackStatus.DEFERRED_OPEN_ORDER
    assert t.history == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/writeback/test_target.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `target.py`**

Create `services/agent-spine/src/trax_io_spine/writeback/__init__.py` (empty), then `services/agent-spine/src/trax_io_spine/writeback/target.py`:
```python
"""Writeback target Protocol + an in-memory implementation for tests and `--dry-run`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from trax_io_spine.contracts import WritebackRequest, WritebackResult, WritebackStatus

_FIELDS = ("rop", "eoq", "safety_stock", "max_stock")


class WritebackTarget(Protocol):
    def write(self, req: WritebackRequest) -> WritebackResult: ...


class InMemoryWritebackTarget:
    """Dict-backed eMRO stand-in. Idempotent by key; defers on a simulated open order."""

    def __init__(self, open_orders: set[tuple[str, str, str]] | None = None) -> None:
        self._open_orders = open_orders or set()
        self._levels: dict[tuple[str, str, str], dict[str, int]] = {}
        self._seen: dict[str, WritebackResult] = {}
        self.history: list[WritebackResult] = []

    def write(self, req: WritebackRequest) -> WritebackResult:
        if req.idempotency_key in self._seen:
            return self._seen[req.idempotency_key]

        key = (req.tenant_id, req.pn, req.location)
        if key in self._open_orders:
            result = WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.DEFERRED_OPEN_ORDER,
            )
            self._seen[req.idempotency_key] = result
            return result

        new_values = {f: getattr(req, f) for f in _FIELDS}
        old_values = self._levels.get(key)
        self._levels[key] = new_values
        result = WritebackResult(
            tenant_id=req.tenant_id, pn=req.pn, location=req.location,
            status=WritebackStatus.WRITTEN, old_values=old_values, new_values=new_values,
            written_at=datetime.now(UTC),
        )
        self._seen[req.idempotency_key] = result
        self.history.append(result)
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/writeback/test_target.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/writeback/ services/agent-spine/tests/writeback/
git commit -m "#4 agent-spine: WritebackTarget Protocol + idempotent in-memory target"
```

---

### Task 8: fake_emro FastAPI harness

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/writeback/fake_emro.py`
- Test: `services/agent-spine/tests/writeback/test_fake_emro.py`

**Interfaces:**
- Produces: `create_fake_emro(open_orders: set[tuple[str, str, str]] | None = None) -> FastAPI`. Endpoint `POST /inventory-levels` accepts a `WritebackRequest`-shaped body and returns `{status, old_values, new_values}`: `200` written, `409` on open order, idempotency replay returns the first result. `GET /history` returns the applied writes.
- Consumes: `WritebackRequest`/`WritebackStatus` shapes (validated by the same fields).

> `fastapi` is imported inside `create_fake_emro` so the module imports without the `emro` extra; the test is gated with `pytest.importorskip("fastapi")`.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/writeback/test_fake_emro.py`:
```python
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from trax_io_spine.writeback.fake_emro import create_fake_emro  # noqa: E402

_BODY = {
    "tenant_id": "acme", "pn": "PN-A", "location": "LOC-1",
    "rop": 5, "eoq": 4, "safety_stock": 2, "max_stock": 12,
    "provenance_id": "p-1", "idempotency_key": "2026-04-01:acme:PN-A:LOC-1",
}


def test_post_writes_and_records_history() -> None:
    client = TestClient(create_fake_emro())
    resp = client.post("/inventory-levels", json=_BODY)
    assert resp.status_code == 200
    assert resp.json()["status"] == "written"
    assert client.get("/history").json() == [{"tenant_id": "acme", "pn": "PN-A",
                                              "location": "LOC-1",
                                              "values": {"rop": 5, "eoq": 4,
                                                         "safety_stock": 2, "max_stock": 12}}]


def test_open_order_returns_409() -> None:
    client = TestClient(create_fake_emro(open_orders={("acme", "PN-A", "LOC-1")}))
    resp = client.post("/inventory-levels", json=_BODY)
    assert resp.status_code == 409


def test_idempotent_replay() -> None:
    client = TestClient(create_fake_emro())
    client.post("/inventory-levels", json=_BODY)
    resp = client.post("/inventory-levels", json={**_BODY, "rop": 999})
    assert resp.json()["new_values"]["rop"] == 5  # original write, replay ignored
    assert len(client.get("/history").json()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra emro pytest tests/writeback/test_fake_emro.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_spine.writeback.fake_emro`.

- [ ] **Step 3: Implement `fake_emro.py`**

Create `services/agent-spine/src/trax_io_spine/writeback/fake_emro.py`:
```python
"""In-memory FastAPI mock of the eMRO Writeback REST surface (#6).

Pins the request/response contract so the writeback client + integration tests run with no
AWS, and #6 implements the same shape. Behind the `emro` extra (FastAPI imported lazily).
"""

from __future__ import annotations

from typing import Any

_FIELDS = ("rop", "eoq", "safety_stock", "max_stock")


def create_fake_emro(open_orders: set[tuple[str, str, str]] | None = None) -> Any:
    from fastapi import FastAPI, Response
    from fastapi.responses import JSONResponse

    blocked = open_orders or set()
    levels: dict[tuple[str, str, str], dict[str, int]] = {}
    seen: dict[str, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []

    app = FastAPI(title="fake_emro")

    @app.post("/inventory-levels")
    def write_level(body: dict[str, Any]) -> Response:
        idem = str(body["idempotency_key"])
        if idem in seen:
            return JSONResponse(seen[idem])
        key = (body["tenant_id"], body["pn"], body["location"])
        if key in blocked:
            return JSONResponse({"status": "deferred_open_order"}, status_code=409)
        new_values = {f: int(body[f]) for f in _FIELDS}
        old_values = levels.get(key)
        levels[key] = new_values
        payload = {"status": "written", "old_values": old_values, "new_values": new_values}
        seen[idem] = payload
        history.append(
            {"tenant_id": key[0], "pn": key[1], "location": key[2], "values": new_values}
        )
        return JSONResponse(payload)

    @app.get("/history")
    def get_history() -> list[dict[str, Any]]:
        return history

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev --extra emro pytest tests/writeback/test_fake_emro.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/writeback/fake_emro.py services/agent-spine/tests/writeback/test_fake_emro.py
git commit -m "#4 agent-spine: fake_emro FastAPI harness (pins the #6 writeback contract)"
```

---

### Task 9: REST writeback client

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/writeback/rest.py`
- Test: `services/agent-spine/tests/writeback/test_rest.py`

**Interfaces:**
- Produces: `RestWritebackClient(base_url: str = "", client: httpx.Client | None = None)` implementing `WritebackTarget.write`. POSTs `req.model_dump()` to `{base_url}/inventory-levels`; maps `200 → WRITTEN` (carrying `old_values`/`new_values`), `409 → DEFERRED_OPEN_ORDER`, anything else → `FAILED` (with `error_message`).
- Consumes: `WritebackRequest`/`WritebackResult`, `fake_emro` (test only).

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/writeback/test_rest.py`:
```python
import httpx
import pytest

pytest.importorskip("fastapi")

from trax_io_spine.contracts import WritebackRequest, WritebackStatus  # noqa: E402
from trax_io_spine.writeback.fake_emro import create_fake_emro  # noqa: E402
from trax_io_spine.writeback.rest import RestWritebackClient  # noqa: E402


def _client(app) -> RestWritebackClient:
    transport = httpx.ASGITransport(app=app)
    return RestWritebackClient(base_url="http://emro", client=httpx.Client(transport=transport))


def _req(**over: object) -> WritebackRequest:
    base: dict[str, object] = dict(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        rop=5, eoq=4, safety_stock=2, max_stock=12, provenance_id="p-1",
        idempotency_key="2026-04-01:acme:PN-A:LOC-1",
    )
    base.update(over)
    return WritebackRequest(**base)  # type: ignore[arg-type]


def test_rest_write_maps_200_to_written() -> None:
    res = _client(create_fake_emro()).write(_req())
    assert res.status is WritebackStatus.WRITTEN
    assert res.new_values == {"rop": 5, "eoq": 4, "safety_stock": 2, "max_stock": 12}


def test_rest_write_maps_409_to_deferred() -> None:
    app = create_fake_emro(open_orders={("acme", "PN-A", "LOC-1")})
    res = _client(app).write(_req())
    assert res.status is WritebackStatus.DEFERRED_OPEN_ORDER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra emro pytest tests/writeback/test_rest.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_spine.writeback.rest`.

- [ ] **Step 3: Implement `rest.py`**

Create `services/agent-spine/src/trax_io_spine/writeback/rest.py`:
```python
"""httpx client to the eMRO Writeback REST surface (real #6, or the fake_emro harness)."""

from __future__ import annotations

import httpx

from trax_io_spine.contracts import WritebackRequest, WritebackResult, WritebackStatus


class RestWritebackClient:
    def __init__(self, base_url: str = "", client: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client()

    def write(self, req: WritebackRequest) -> WritebackResult:
        try:
            resp = self._client.post(
                f"{self._base_url}/inventory-levels", json=req.model_dump()
            )
        except httpx.HTTPError as exc:
            return WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.FAILED, error_message=str(exc),
            )
        if resp.status_code == 200:
            body = resp.json()
            return WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.WRITTEN,
                old_values=body.get("old_values"), new_values=body.get("new_values"),
            )
        if resp.status_code == 409:
            return WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.DEFERRED_OPEN_ORDER,
            )
        return WritebackResult(
            tenant_id=req.tenant_id, pn=req.pn, location=req.location,
            status=WritebackStatus.FAILED, error_message=f"http {resp.status_code}",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev --extra emro pytest tests/writeback/test_rest.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/writeback/rest.py services/agent-spine/tests/writeback/test_rest.py
git commit -m "#4 agent-spine: RestWritebackClient (200->written, 409->deferred, else failed)"
```

---

## Phase 5: Supervisor

### Task 10: Supervisor orchestration

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/supervisor.py`
- Test: `services/agent-spine/tests/test_supervisor.py`

**Interfaces:**
- Produces:
  - `to_writeback_request(rec: Recommendation, *, idempotency_key: str) -> WritebackRequest` (from `rec.policy`).
  - `Supervisor(*, feature_store, inventory_state, enforcer=None, writeback=None, config=None)` with `run(self, *, tenant: TenantContext, keys: list[tuple[str, str]], now: datetime, reporting_horizon_days: int = 30) -> OrchestrationResult`.
- Behavior: bind `tenant_scope(tenant)`; `RecommendationService(feature_store=..., inventory_state=..., config=config).run(tenant=tenant, keys=keys, now=now, reporting_horizon_days=...)`; for each rec, `enforce()`; route — rejected → `rejected`; queued → `queued` (the outcome's `approval_task`); approved → `writeback.write(to_writeback_request(rec, idempotency_key=f"{now.date()}:{tenant}:{pn}:{loc}"))` and bucket by result status (`written`/`deferred`/`failed`); carry `batch.skipped`; fill `summary` counts.
- Consumes: `RecommendationService` (#11), `GuardrailEnforcer`, `WritebackTarget`, `InMemoryWritebackTarget` (default), identity, contracts.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/test_supervisor.py`:
```python
from datetime import UTC, datetime

from trax_io_feature_store import InMemoryFeatureStore, TenantContext
from trax_io_reco.contracts.recommendation import Recommendation, RecommendationBatch

from trax_io_spine.contracts import WritebackStatus
from trax_io_spine.supervisor import Supervisor, to_writeback_request
from trax_io_spine.writeback.target import InMemoryWritebackTarget

from tests.conftest import make_current, make_policy


class _FakeService:
    """Stands in for #11's RecommendationService.run, returning a fixed batch."""

    def __init__(self, recs: tuple[Recommendation, ...]) -> None:
        self._recs = recs

    def run(self, *, tenant, keys, now, reporting_horizon_days=30) -> RecommendationBatch:  # noqa: ANN001
        from trax_io_reco.contracts.recommendation import BatchSummary

        return RecommendationBatch(
            tenant_id=tenant.tenant_id, generated_at=now, recommendations=self._recs,
            skipped=(), summary=BatchSummary(total=len(self._recs)),
        )


def test_to_writeback_request_maps_policy(make_rec) -> None:
    rec = make_rec(policy=make_policy(rop=7, eoq=3, safety_stock=2, max_stock=15))
    req = to_writeback_request(rec, idempotency_key="k1")
    assert (req.rop, req.eoq, req.safety_stock, req.max_stock) == (7, 3, 2, 15)
    assert req.idempotency_key == "k1"


def test_supervisor_routes_and_writes(make_rec) -> None:
    approved = make_rec(
        recommendation_id="r-approve",
        policy=make_policy(max_stock=23), current_policy=make_current(max_stock=20),  # +15%
    )
    writeback = InMemoryWritebackTarget()
    sup = Supervisor(
        feature_store=InMemoryFeatureStore(), inventory_state=None,
        writeback=writeback, service=_FakeService((approved,)),
    )
    res = sup.run(
        tenant=TenantContext(tenant_id="acme"), keys=[("PN-A", "LOC-1")],
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    assert len(res.written) == 1
    assert res.written[0].status is WritebackStatus.WRITTEN
    assert res.summary["written"] == 1
```

> The test injects a `service=` to avoid running the full #11 engine in a unit test. Implement `Supervisor.__init__` to accept an optional `service`; when omitted it builds a real `RecommendationService` from `feature_store`/`inventory_state`/`config`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/test_supervisor.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_spine.supervisor`.

- [ ] **Step 3: Implement `supervisor.py`**

Create `services/agent-spine/src/trax_io_spine/supervisor.py`:
```python
"""Deterministic Supervisor: #2 -> #11 -> guardrail -> writeback -> OrchestrationResult.

The seam an LLM Supervisor wraps later. All collaborators are injected; by default it builds
the real #11 RecommendationService and writes to an in-memory target.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from trax_io_feature_store import FeatureStoreClient, TenantContext
from trax_io_reco.contracts.recommendation import Recommendation
from trax_io_reco.service import RecommendationService

from trax_io_spine.contracts import (
    GuardrailStatus,
    OrchestrationResult,
    WritebackRequest,
    WritebackResult,
    WritebackStatus,
)
from trax_io_spine.guardrail.enforce import GuardrailEnforcer
from trax_io_spine.identity import tenant_scope
from trax_io_spine.writeback.target import InMemoryWritebackTarget, WritebackTarget

_FIELDS = ("rop", "eoq", "safety_stock", "max_stock")


def to_writeback_request(rec: Recommendation, *, idempotency_key: str) -> WritebackRequest:
    if rec.policy is None:  # pragma: no cover -- supervisor only calls this for approved policies
        raise ValueError("recommendation has no policy to write")
    p = rec.policy
    return WritebackRequest(
        tenant_id=rec.tenant_id, pn=rec.part_number, location=rec.current_location,
        rop=p.rop, eoq=p.eoq, safety_stock=p.safety_stock, max_stock=p.max_stock,
        provenance_id=p.provenance_id, idempotency_key=idempotency_key,
    )


class Supervisor:
    def __init__(
        self,
        *,
        feature_store: FeatureStoreClient,
        inventory_state: Any,
        enforcer: GuardrailEnforcer | None = None,
        writeback: WritebackTarget | None = None,
        config: Any = None,
        service: Any = None,
    ) -> None:
        self._service = service or RecommendationService(
            feature_store=feature_store, inventory_state=inventory_state, config=config
        )
        self._enforcer = enforcer or GuardrailEnforcer()
        self._writeback: WritebackTarget = writeback or InMemoryWritebackTarget()

    def run(
        self,
        *,
        tenant: TenantContext,
        keys: list[tuple[str, str]],
        now: datetime,
        reporting_horizon_days: int = 30,
    ) -> OrchestrationResult:
        with tenant_scope(tenant):
            batch = self._service.run(
                tenant=tenant, keys=keys, now=now,
                reporting_horizon_days=reporting_horizon_days,
            )
            written: list[WritebackResult] = []
            deferred: list[WritebackResult] = []
            failed: list[WritebackResult] = []
            queued = []
            rejected = []

            for rec in batch.recommendations:
                outcome = self._enforcer.enforce(rec)
                if outcome.status is GuardrailStatus.REJECTED_HARD_GUARDRAIL:
                    rejected.append(outcome)
                elif outcome.status is GuardrailStatus.QUEUED_FOR_APPROVAL:
                    if outcome.approval_task is not None:
                        queued.append(outcome.approval_task)
                else:  # APPROVED_FOR_WRITE
                    idem = f"{now.date()}:{tenant.tenant_id}:{rec.part_number}:{rec.current_location}"
                    result = self._writeback.write(to_writeback_request(rec, idempotency_key=idem))
                    if result.status is WritebackStatus.WRITTEN:
                        written.append(result)
                    elif result.status is WritebackStatus.DEFERRED_OPEN_ORDER:
                        deferred.append(result)
                    else:
                        failed.append(result)

            summary = Counter(
                {
                    "recommendations": len(batch.recommendations),
                    "written": len(written),
                    "deferred": len(deferred),
                    "failed": len(failed),
                    "queued": len(queued),
                    "rejected": len(rejected),
                    "skipped": len(batch.skipped),
                }
            )
            return OrchestrationResult(
                tenant_id=tenant.tenant_id, generated_at=now,
                written=tuple(written), deferred=tuple(deferred), failed=tuple(failed),
                queued=tuple(queued), rejected=tuple(rejected), skipped=batch.skipped,
                summary=dict(summary),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/test_supervisor.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/supervisor.py services/agent-spine/tests/test_supervisor.py
git commit -m "#4 agent-spine: Supervisor orchestration (#2 -> #11 -> guardrail -> writeback)"
```

---

## Phase 6: CLI

### Task 11: `trax-io-spine` CLI

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/cli.py`
- Test: `services/agent-spine/tests/test_cli.py`

**Interfaces:**
- Produces: a typer `app` with a `run` command: `--extract-dir PATH`, `--tenant TEXT`, `--now TEXT` (ISO, optional), `--apply/--dry-run` (default `--dry-run`), `--writeback-url TEXT` (used with `--apply`). Builds stores via `build_stores_from_extract`, runs `Supervisor.run`, prints the `summary` JSON. `--dry-run` uses `InMemoryWritebackTarget`; `--apply` uses `RestWritebackClient(writeback_url)`.
- Consumes: `build_stores_from_extract`, `Supervisor`, `InMemoryWritebackTarget`, `RestWritebackClient`.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/test_cli.py`:
```python
import json

from typer.testing import CliRunner

from trax_io_spine.cli import app

_SAMPLE = "../recommendation-engine/examples/extract_sample"


def test_cli_dry_run_emits_summary() -> None:
    result = CliRunner().invoke(
        app, ["run", "--extract-dir", _SAMPLE, "--tenant", "acme", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert "recommendations" in summary
    assert summary["recommendations"] >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_spine.cli`.

- [ ] **Step 3: Implement `cli.py`**

Create `services/agent-spine/src/trax_io_spine/cli.py`:
```python
"""`trax-io-spine` CLI — offline end-to-end orchestration over an extract dir."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer
from trax_io_feature_store import TenantContext
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.supervisor import Supervisor
from trax_io_spine.writeback.rest import RestWritebackClient
from trax_io_spine.writeback.target import InMemoryWritebackTarget

app = typer.Typer(help="Trax IO Agent Spine — deterministic orchestration CLI.")


@app.command()
def run(
    extract_dir: str = typer.Option(..., "--extract-dir"),
    tenant: str = typer.Option(..., "--tenant"),
    now: str | None = typer.Option(None, "--now", help="ISO timestamp; defaults to now (UTC)"),
    apply: bool = typer.Option(False, "--apply/--dry-run"),
    writeback_url: str = typer.Option("http://localhost:9000", "--writeback-url"),
) -> None:
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir, tenant_id=tenant)
    stamp = datetime.fromisoformat(now) if now else datetime.now(UTC)
    target = RestWritebackClient(writeback_url) if apply else InMemoryWritebackTarget()
    supervisor = Supervisor(feature_store=fs, inventory_state=inv, writeback=target)
    result = supervisor.run(tenant=TenantContext(tenant_id=tenant_id), keys=keys, now=stamp)
    typer.echo(json.dumps(result.summary))


if __name__ == "__main__":  # pragma: no cover
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/test_cli.py -q`
Expected: 1 passed.

- [ ] **Step 5: Verify the CLI end-to-end by hand**

Run:
```bash
cd services/agent-spine && uv run trax-io-spine run \
  --extract-dir ../recommendation-engine/examples/extract_sample --tenant acme --dry-run
```
Expected: a one-line JSON summary, e.g. `{"recommendations": 6, "written": ..., "queued": ..., ...}`.

- [ ] **Step 6: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/cli.py services/agent-spine/tests/test_cli.py
git commit -m "#4 agent-spine: trax-io-spine CLI (offline end-to-end orchestration, milestone #8)"
```

---

## Phase 7: Integration

### Task 12: End-to-end + tenant isolation integration tests

**Files:**
- Create: `services/agent-spine/tests/test_integration.py`

**Interfaces:**
- Consumes: everything. Exercises `Supervisor.run` over #11's committed extract sample with a real `RecommendationService`, asserting routing + that a cross-tenant run produces no writes (isolation).

- [ ] **Step 1: Write the integration test**

Create `services/agent-spine/tests/test_integration.py`:
```python
from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.supervisor import Supervisor
from trax_io_spine.writeback.target import InMemoryWritebackTarget

_SAMPLE = Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"


def test_end_to_end_orchestration_routes_recommendations() -> None:
    fs, inv, tenant_id, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    writeback = InMemoryWritebackTarget()
    sup = Supervisor(feature_store=fs, inventory_state=inv, writeback=writeback)
    res = sup.run(
        tenant=TenantContext(tenant_id=tenant_id), keys=keys,
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    total = res.summary["recommendations"]
    routed = (
        res.summary["written"] + res.summary["deferred"] + res.summary["failed"]
        + res.summary["queued"] + res.summary["rejected"]
    )
    assert total >= 1
    assert routed == total  # every recommendation lands in exactly one bucket
    # writes that happened are recorded in the target's history
    assert len(writeback.history) == res.summary["written"]


def test_cross_tenant_run_writes_nothing() -> None:
    fs, inv, tenant_id, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    writeback = InMemoryWritebackTarget()
    sup = Supervisor(feature_store=fs, inventory_state=inv, writeback=writeback)
    # A different tenant has no data in `fs` -> every key skipped, nothing written.
    res = sup.run(
        tenant=TenantContext(tenant_id="other-airline"), keys=keys,
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    assert res.summary["written"] == 0
    assert writeback.history == []
    assert res.summary["skipped"] == len(keys)
```

- [ ] **Step 2: Run the integration test**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/test_integration.py -q`
Expected: 2 passed.

- [ ] **Step 3: Run the full suite + lint**

Run:
```bash
cd services/agent-spine && uv run --extra dev --extra emro pytest -q && uv run --extra dev ruff check .
```
Expected: all green, ruff clean.

- [ ] **Step 4: Commit**

```bash
git add services/agent-spine/tests/test_integration.py
git commit -m "#4 agent-spine: end-to-end + tenant-isolation integration tests"
```

---

## Post-implementation

- [ ] Update `CLAUDE.md` Section A run/test table with the `services/agent-spine/` commands (`uv run --extra dev pytest`, `--extra emro` for the writeback/integration tests; `uv run --extra dev ruff check .`).
- [ ] Update `ROADMAP.md` (#4 Agent Spine: mark the deterministic-core slice done with today's date; note the deferred LLM/AgentCore/Cedar/CDK/event-lane items) and `TASKS.md`.
- [ ] Write `docs/adr/2026-06-27-0005-deterministic-agent-spine-core.md` recording the deterministic-core-behind-Protocols decision (mirrors ADR-0004's rationale).
- [ ] Run an adversarial review of the guardrail enforcement + writeback idempotency (the established cadence) before declaring the slice done.

---

## Self-Review notes (author)

- **Spec coverage:** identity §4.1 → T3; contracts §4.2 → T2; guardrail hard+band §4.3 → T4/T5/T6; writeback §4.4 → T7/T8/T9; supervisor §4.5 → T10; CLI §4.6 → T11; testing §6 → tests in every task + T12; milestone #8 → T11 step 5.
- **Deferred items** (§2 out-of-scope) are intentionally absent: Strands/AgentCore, Cedar, Memory, event lane, CDK — listed in post-implementation/ROADMAP, not built here.
- **Type consistency:** `GuardrailStatus` (3 values), `WritebackStatus` (3 values), `AutonomyTier` (from #11), `compute_delta_pct`/`hard_guardrail_violations`/`aog_forces_advisor`, `BandAutonomyPolicy.authorize`, `GuardrailEnforcer.enforce`, `WritebackTarget.write`, `Supervisor.run`, `to_writeback_request` — names used identically across tasks.
- **Spec refinement noted in T2:** the spec's 4-value `GuardrailStatus` (incl. `deferred`) is realized as a 3-value `GuardrailStatus` + `deferred_open_order` on `WritebackStatus`, since deferral is an eMRO writeback outcome, not an enforcement decision.
