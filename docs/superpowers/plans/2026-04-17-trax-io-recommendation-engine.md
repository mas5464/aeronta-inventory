# Trax IO Recommendation Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, no-LLM recommendation engine that turns eMRO feature data into ranked Purchase / Transfer / Reduce Stock / Sell / Adjust Min-Max recommendations, each with evidence, AOG risk, and a confidence score.

**Architecture:** A library package `trax_io_reco` under `services/recommendation-engine/`. One shared primitive — the **net position** per `(PN, Location)` — feeds five pluggable recommenders; an arbitration stage removes contradictions; an AOG scorer and confidence/ranker finish the batch. Policy math (the Adjust Min-Max anchor) is deterministic and forward-compatible with the Agent Spine contracts. Inputs come through the real `trax_io_feature_store.FeatureStoreClient` for what it serves and an engine-owned `InventoryStateProvider` stub for the gaps (on-hand stock, current policy, scheduled demand, AOG, repair TAT).

**Tech Stack:** Python 3.12 · pydantic v2 · scipy ≥1.13 (stats/quantiles) · python-ulid · click (CLI) · FastAPI (optional `api` extra) · pytest · ruff · `uv` · hatchling.

**Spec:** [docs/superpowers/specs/2026-04-17-trax-io-recommendation-engine-design.md](../specs/2026-04-17-trax-io-recommendation-engine-design.md). Read it before starting; section references below (e.g. "spec §6.4") point into it.

## Global Constraints

- Python `requires-python = ">=3.12"`; ruff `target-version = "py312"`, `line-length = 100`, `lint.select = ["E","F","I","B","UP","N","SIM"]`. No mypy.
- Every module begins with `from __future__ import annotations`. Absolute imports only (`from trax_io_reco.x import y`).
- All pydantic models: `model_config = ConfigDict(frozen=True, extra="forbid")`. Money is `Decimal`; quantities `NonNegativeInt`.
- Dist name `trax-io-reco`; import package `trax_io_reco`; src layout; wheel `packages = ["src/trax_io_reco"]`; build backend `hatchling`.
- Dev deps via `[project.optional-dependencies] dev = ["pytest>=8.2.0","ruff>=0.4.0"]` (NOT PEP 735 groups). Optional `api = ["fastapi>=0.110.0","uvicorn>=0.29.0"]`.
- pytest: `[tool.pytest.ini_options] testpaths=["tests"] pythonpath=["src"]`.
- Test command: `uv run --extra dev pytest`. Lint: `uv run --extra dev ruff check .`.
- ULIDs: `from ulid import ULID` → `str(ULID())`.
- Logging: stdlib `logging.getLogger("trax_io.reco.<area>")` — NO structlog.
- **Reuse, don't redefine, the feature-store schemas** for FS-served context (`from trax_io_feature_store.schemas import DemandHistory, CausalUtilization, LeadTimeDistribution, VendorEconomics, PartAttributes, Criticality, InterchangeableGraph, LocationGraph, OpenOrdersSnapshot`). The engine defines only gap models, computed models, and recommendation contracts.
- `trax-io-reco` depends on `trax-io-feature-store` via a local path/editable dependency.
- Determinism is a hard requirement: no reliance on dict/set iteration order for outputs; explicit tie-breaks; ranking score in `Decimal`; `input_snapshot_hash` over canonical JSON excluding volatile fields.

---

## File Structure

```
services/recommendation-engine/
├── pyproject.toml · uv.lock · README.md
├── src/trax_io_reco/
│   ├── __init__.py
│   ├── contracts/{__init__,enums,policy,recommendation,context}.py
│   ├── data/{__init__,feature_reader,inventory_state,assembler}.py
│   ├── regime/{__init__,classifier}.py
│   ├── demand/{__init__,projection}.py
│   ├── position/{__init__,net_position}.py
│   ├── policy/{__init__,service_level,lead_time,base_stock,s_S,R_Q,constraints,mini_engine}.py
│   ├── recommenders/{__init__,base,adjust_min_max,purchase,transfer,reduce_sell}.py
│   ├── arbitration.py · risk/{__init__,aog}.py · confidence.py · ranking.py · service.py · cli.py
│   └── api/{__init__,app}.py            # optional; only imported when `api` extra installed
└── tests/
    ├── __init__.py · fixtures/{__init__,scenarios}.py
    └── test_*.py  (one per module + test_eight_scenarios, test_invariants, test_contracts)
```

---

## Phase 0: Governance + Bootstrap

### Task 0: ADR-0004 + roadmap amendment (governance gate)

**Files:**
- Create: `docs/adr/2026-04-17-0004-deterministic-recommendation-layer.md`
- Modify: `ROADMAP.md` (add the sub-project under Wave 1), `docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md` (note the amendment)

- [ ] **Step 1: Write ADR-0004** with this content:

```markdown
# ADR-0004 — Deterministic Recommendation Layer in v1

**Date:** 2026-04-17 · **Status:** Accepted · **Owner:** Miguel Sosa

## Context
Locked design §8 phases AOG→v3, Transfer/Reduce/Sell→v4, Purchase/sourcing→v5; v1 (Q1)
is "dynamic stock-level tuning" (Adjust Min/Max). The owner has chosen to ship a
deterministic recommendation layer in v1 that produces all five recommendation types
as rule-based precursors over the net position, deferring the ML forecasting ensemble (#5),
Bedrock/Strands runtime (#4), writeback (#6), and Planner UI (#7).

## Decision
Add a new sub-project — **Recommendation Engine (deterministic v1)** — at
`services/recommendation-engine/`, seated in Wave 1. It depends on #2 (Feature Store
contract) and is forward-compatible with #4/#5 contracts so its policy core promotes
unchanged. It is read-only (no eMRO writes). The five recommendation types are
deterministic v1 precursors; the locked v3/v4/v5 specialists supersede them with ML/agentic
versions later.

## Consequences
- The 10-sub-project register grows by one; roadmap amended accordingly.
- Deterministic precursors set expectations and acceptance tests the later phases must keep.
- The §5.5 vs §6.1 delta-band reconciliation and the AOG/TAT/scheduled-demand data sources
  remain open items owned by #4 and a future extract domain (spec §10–§11).
```

- [ ] **Step 2: Amend `ROADMAP.md`** — under "Wave 1", add:

```markdown
### Sub-project #11 — Recommendation Engine (deterministic v1) (P1, AI platform) 🏗️
Spec: [2026-04-17-trax-io-recommendation-engine-design.md](docs/superpowers/specs/2026-04-17-trax-io-recommendation-engine-design.md) · ADR: [0004](docs/adr/2026-04-17-0004-deterministic-recommendation-layer.md)
- [ ] Deterministic 5-type recommendation engine (`services/recommendation-engine/`) — see plan
```

- [ ] **Step 3: Commit** (skip `git` commands if repo is not initialized — note it instead).

### Task 1: Package scaffold

**Files:**
- Create: `services/recommendation-engine/pyproject.toml`, `README.md`, `src/trax_io_reco/__init__.py`, and empty `__init__.py` in every subpackage (`contracts`, `data`, `regime`, `demand`, `position`, `policy`, `recommenders`, `risk`, `api`), `tests/__init__.py`, `tests/fixtures/__init__.py`.

**Interfaces:**
- Produces: an installable `trax_io_reco` package with `uv` env and green (empty) test run.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "trax-io-reco"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "pydantic>=2.7.0",
  "scipy>=1.13.0",
  "python-ulid>=3.0.0",
  "click>=8.1.0",
  "trax-io-feature-store",
]

[project.optional-dependencies]
dev = ["pytest>=8.2.0", "ruff>=0.4.0"]
api = ["fastapi>=0.110.0", "uvicorn>=0.29.0"]

[project.scripts]
trax-io-reco = "trax_io_reco.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/trax_io_reco"]

[tool.uv.sources]
trax-io-feature-store = { path = "../feature-store", editable = true }

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "SIM"]
```

- [ ] **Step 2: Create all `__init__.py` files** (empty except `src/trax_io_reco/__init__.py` which gets `__version__ = "0.1.0"`). Write a one-line `tests/test_smoke.py`:

```python
def test_imports() -> None:
    import trax_io_reco
    assert trax_io_reco.__version__ == "0.1.0"
```

- [ ] **Step 3: Sync + run.** Run: `cd services/recommendation-engine && uv sync --extra dev && uv run --extra dev pytest -q`. Expected: 1 passed. If `trax-io-feature-store` path resolution fails, confirm the relative path `../feature-store` is correct.

- [ ] **Step 4: Commit.**

---

## Phase 1: Contracts

> All models frozen + `extra="forbid"`. Reuse FS schemas for FS-served groups.

### Task 2: Enums (`contracts/enums.py`)

**Interfaces:**
- Produces: `Regime, CanonicalCriticality, PolicyKind, AutonomyTier, ForecastHorizon, EvidenceKind, AogRiskLevel, RecommendationType`.

- [ ] **Step 1: Write the failing test** `tests/test_contracts.py`:

```python
from trax_io_reco.contracts.enums import (
    Regime, CanonicalCriticality, PolicyKind, AutonomyTier,
    ForecastHorizon, EvidenceKind, AogRiskLevel, RecommendationType,
)


def test_regime_values():
    assert [r.value for r in Regime] == ["ultra_rare", "intermittent", "moderate", "high_volume"]


def test_forecast_horizon_named_members():
    # Pinned for forward-compat promotion to trax_io.contracts.forecast (spec §5.1)
    assert ForecastHorizon.DAYS_30 == 30 and ForecastHorizon.DAYS_180 == 180
    assert {m.name: m.value for m in ForecastHorizon} == {
        "DAYS_30": 30, "DAYS_60": 60, "DAYS_90": 90, "DAYS_180": 180}


def test_criticality_ordered():
    assert CanonicalCriticality.TIER_1 < CanonicalCriticality.TIER_2


def test_policy_kind_values():
    assert {k.value for k in PolicyKind} == {"base_stock", "s_S", "R_Q"}
```

- [ ] **Step 2: Run → FAIL** (`pytest tests/test_contracts.py -v`).
- [ ] **Step 3: Implement**

```python
from __future__ import annotations
from enum import IntEnum, StrEnum


class Regime(StrEnum):
    ULTRA_RARE = "ultra_rare"
    INTERMITTENT = "intermittent"
    MODERATE = "moderate"
    HIGH_VOLUME = "high_volume"


class CanonicalCriticality(IntEnum):
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4
    TIER_5 = 5


class PolicyKind(StrEnum):
    BASE_STOCK = "base_stock"
    S_S = "s_S"
    R_Q = "R_Q"


class AutonomyTier(IntEnum):
    ADVISOR = 1
    BOUNDED = 2
    AUTONOMOUS = 3


class ForecastHorizon(IntEnum):
    DAYS_30 = 30
    DAYS_60 = 60
    DAYS_90 = 90
    DAYS_180 = 180


class EvidenceKind(StrEnum):
    WORK_ORDER = "work_order"
    MAINTENANCE_EVENT = "maintenance_event"
    TASK_CARD = "task_card"
    OPEN_ORDER = "open_order"
    DEMAND_HISTORY = "demand_history"
    DONOR_STOCK = "donor_stock"
    SHELF_LIFE = "shelf_life"
    AOG_EVENT = "aog_event"


class AogRiskLevel(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class RecommendationType(StrEnum):
    PURCHASE = "purchase"
    TRANSFER = "transfer"
    REDUCE_STOCK = "reduce_stock"
    SELL = "sell"
    ADJUST_MIN_MAX = "adjust_min_max"
```

- [ ] **Step 4: Run → PASS. Step 5: Commit.**

### Task 3: Policy mirror (`contracts/policy.py`)

**Interfaces:**
- Consumes: `PolicyKind` (Task 2).
- Produces: `PolicyRecommendation` (frozen, with `rop≥safety_stock` and `max_stock≥rop+eoq` validator; `model_id` default `"stub"`).

- [ ] **Step 1: Failing test** (append to `tests/test_contracts.py`):

```python
import pytest
from pydantic import ValidationError
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.enums import PolicyKind


def _pr(**kw):
    base = dict(tenant_id="t", pn="P", location="L", rop=10, eoq=5, safety_stock=8,
                max_stock=15, policy_kind=PolicyKind.S_S, provenance_id="prov")
    base.update(kw)
    return PolicyRecommendation(**base)


def test_policy_default_model_id_is_stub():
    assert _pr().model_id == "stub"


def test_policy_validator_rop_ge_ss():
    with pytest.raises(ValidationError):
        _pr(rop=5, safety_stock=8)


def test_policy_validator_max_ge_rop_plus_eoq():
    with pytest.raises(ValidationError):
        _pr(rop=10, eoq=5, max_stock=14)
```

- [ ] **Step 2: Run → FAIL. Step 3: Implement**

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, NonNegativeInt, model_validator
from trax_io_reco.contracts.enums import PolicyKind


class PolicyRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: str
    pn: str
    location: str
    rop: NonNegativeInt
    eoq: NonNegativeInt
    safety_stock: NonNegativeInt
    max_stock: NonNegativeInt
    policy_kind: PolicyKind
    service_level_target: float = 0.95
    provenance_id: str
    model_id: str = "stub"

    @model_validator(mode="after")
    def _floors(self) -> "PolicyRecommendation":
        if self.rop < self.safety_stock:
            raise ValueError("rop must be >= safety_stock")
        if self.max_stock < self.rop + self.eoq:
            raise ValueError("max_stock must be >= rop + eoq")
        return self
```

- [ ] **Step 4: Run → PASS. Step 5: Commit.**

### Task 4: Context contracts (`contracts/context.py`)

**Interfaces:**
- Consumes: FS schemas, `RegimeClassifier` output later.
- Produces: gap models `StockPosition, CurrentPolicy, ScheduledDemandItem, AogSignal, RepairTat, TenantPolicyConfig`; computed `DemandProjection, NetPosition`; wrapper `PartLocationContext`. (Field definitions are the single source of truth — spec §5.3.)

- [ ] **Step 1: Failing test** asserting key fields exist & frozen:

```python
from datetime import date
from decimal import Decimal
from trax_io_reco.contracts.context import (
    StockPosition, CurrentPolicy, TenantPolicyConfig, DemandProjection, NetPosition,
)


def test_stock_position_available_inputs():
    sp = StockPosition(on_hand=10, serviceable=8, unserviceable_in_repair=2,
                       allocated_reserved=3, rental=0, loan=0)
    assert sp.serviceable - sp.allocated_reserved == 5


def test_demand_projection_is_rate():
    dp = DemandProjection(mean_per_day=0.5, std_per_day=0.7, dist_kind="COMPOUND_POISSON",
                          dist_params={"lambda": 0.4, "clump_p": 0.8},
                          historical_component=0.5, scheduled_component=0.0,
                          by_aircraft={}, by_task={}, basis_window_days=730)
    assert dp.mean_per_day == 0.5
```

- [ ] **Step 2: Run → FAIL. Step 3: Implement** (frozen models). Key shapes (spec §5.3):

```python
from __future__ import annotations
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, NonNegativeFloat
from trax_io_reco.contracts.enums import EvidenceKind
from trax_io_feature_store.schemas import (  # reuse FS schemas (Global Constraints)
    DemandHistory, CausalUtilization, LeadTimeDistribution, VendorEconomics,
    PartAttributes, Criticality, InterchangeableGraph, LocationGraph, OpenOrdersSnapshot,
)


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StockPosition(_Base):
    on_hand: NonNegativeInt
    serviceable: NonNegativeInt
    unserviceable_in_repair: NonNegativeInt = 0
    allocated_reserved: NonNegativeInt = 0
    rental: NonNegativeInt = 0
    loan: NonNegativeInt = 0


class CurrentPolicy(_Base):
    rop: NonNegativeInt
    eoq: NonNegativeInt
    safety_stock: NonNegativeInt
    max_stock: NonNegativeInt
    replenishment_lead_days: NonNegativeFloat = 0.0


class ScheduledDemandItem(_Base):
    due_date: date
    qty: NonNegativeInt
    source_ref: str
    source_kind: EvidenceKind
    ac_type: str | None = None


class AogSignal(_Base):
    active: bool = False
    last_event_date: date | None = None
    events_24mo: NonNegativeInt = 0
    last_shortage_at: datetime | None = None


class RepairTat(_Base):
    mean_days: NonNegativeFloat = 0.0
    p90_days: NonNegativeFloat = 0.0
    n_observations: NonNegativeInt = 0


class TenantPolicyConfig(_Base):
    service_level_by_tier: dict[int, float] = Field(
        default_factory=lambda: {1: 0.995, 2: 0.98, 3: 0.95, 4: 0.92, 5: 0.90})
    holding_cost_rate: float = 0.25
    ordering_cost: float = 150.0
    high_value_threshold: float = 5000.0
    currency: str = "USD"


class DemandProjection(_Base):
    mean_per_day: float
    std_per_day: float
    dist_kind: Literal["NORMAL", "COMPOUND_POISSON", "NBD", "EMPIRICAL"]
    dist_params: dict[str, float]
    historical_component: float
    scheduled_component: float
    by_aircraft: dict[str, float]
    by_task: dict[str, float]
    basis_window_days: int


class NetPosition(_Base):
    pn: str
    location: str
    group_id: str | None
    window_days: int
    available: float
    expected_receipts_in_window: float
    projected_demand: float
    net: float
    shortage: float


class PartLocationContext(_Base):
    tenant_id: str
    pn: str
    location: str
    stock_position: StockPosition
    current_policy: CurrentPolicy
    vendor_economics: VendorEconomics
    part_attributes: PartAttributes
    criticality: Criticality
    lead_time: LeadTimeDistribution | None
    location_graph: LocationGraph | None
    open_orders: OpenOrdersSnapshot | None
    interchange_group: InterchangeableGraph | None
    demand_history: DemandHistory
    causal: CausalUtilization | None
    scheduled_demand: tuple[ScheduledDemandItem, ...] = ()
    aog_signal: AogSignal = AogSignal()
    repair_tat: RepairTat = RepairTat()
    tenant_policy_config: TenantPolicyConfig = TenantPolicyConfig()
```

- [ ] **Step 4: Run → PASS. Step 5: Commit.**

### Task 5: Recommendation contracts (`contracts/recommendation.py`)

**Interfaces:**
- Consumes: enums, `PolicyRecommendation`, `CurrentPolicy`.
- Produces: `Evidence, Recommendation, SkippedKey, BatchSummary, RecommendationBatch`.

- [ ] **Step 1: Failing test** — construct a minimal `Recommendation` and assert required non-empty fields + `recommended_location` rule:

```python
from datetime import datetime
from decimal import Decimal
from trax_io_reco.contracts.recommendation import Recommendation, Evidence
from trax_io_reco.contracts.enums import RecommendationType, AogRiskLevel, AutonomyTier, EvidenceKind


def test_recommendation_requires_description_and_evidence():
    rec = Recommendation(
        recommendation_id="01J", tenant_id="t", type=RecommendationType.PURCHASE,
        part_number="P", description="WIDGET", current_location="L",
        recommended_location=None, current_stock=0, projected_demand=5.0,
        shortage_quantity=5.0, recommended_quantity=5.0, estimated_cost_impact=Decimal("500"),
        aog_risk_level=AogRiskLevel.LOW, reason="net<0", confidence_score=0.7,
        supporting_evidence=(Evidence(kind=EvidenceKind.OPEN_ORDER, ref_id="O1", detail="short", as_of=None),),
        horizon_days=90, suggested_autonomy_tier=AutonomyTier.BOUNDED, guardrail_flags=(),
        generated_at=datetime(2026, 4, 17), input_snapshot_hash="h", policy=None, current_policy=None)
    assert rec.description and rec.supporting_evidence
```

- [ ] **Step 2: Run → FAIL. Step 3: Implement** the three models (frozen). `Recommendation` carries exactly the spec §5.2 fields (`description: str` required; `supporting_evidence: tuple[Evidence, ...]`; `policy`/`current_policy` only set for ADJUST). `RecommendationBatch` has `recommendations`, `skipped: tuple[SkippedKey,...]` (`pn, location, reason`), `summary: BatchSummary` (`by_type: dict[str,int]`, `by_aog: dict[int,int]`, `total: int`), `reporting_horizon_days: int = 30`.
- [ ] **Step 4: Run → PASS. Step 5: Commit.**

---

## Phase 2: Data access

### Task 6: `InventoryStateProvider` + `InMemoryInventoryState` (`data/inventory_state.py`)

**Interfaces:**
- Produces: `InventoryStateProvider` (Protocol) with `get_stock_position(*, tenant, pn, location) -> StockPosition`, `get_current_policy(*, tenant, pn, location) -> CurrentPolicy`, `get_scheduled_demand(*, tenant, pn, location) -> tuple[ScheduledDemandItem, ...]`, `get_aog_signal(*, tenant, pn, location) -> AogSignal`, `get_repair_tat(*, tenant, pn, vendor) -> RepairTat`. `InMemoryInventoryState` implements it with a `.seed(kind, key, value)` method. `tenant` is `TenantContext` from `trax_io_feature_store`. Missing key returns the model's default (empty), never raises (gap inputs are optional).

- [ ] **Step 1: Failing test** seeding + reading a `StockPosition` and a default empty `AogSignal`.
- [ ] **Step 2: FAIL. Step 3: Implement** `@runtime_checkable` Protocol + dict-backed stub keyed `(kind, tenant_id, key_tuple)`. Defaults: unseeded `stock_position`/`current_policy` raise `KeyError`-wrapped `InventoryStateLookupError` (these are required); unseeded `scheduled_demand`→`()`, `aog_signal`→`AogSignal()`, `repair_tat`→`RepairTat()`.
- [ ] **Step 4: PASS. Step 5: Commit.**

### Task 7: `FeatureReader` (`data/feature_reader.py`)

**Interfaces:**
- Consumes: `trax_io_feature_store.FeatureStoreClient`, `TenantContext`.
- Produces: `FeatureReader` wrapping the **9** FS methods (spec §5.4: `get_demand_history, get_causal_utilization, get_lead_time_distribution, get_vendor_economics, get_part_attributes, get_criticality, get_interchangeable_graph, get_location_graph, get_open_orders_snapshot`). Each wrapper catches `FeatureStoreLookupError` and returns `None` for the optional groups (`causal`, `lead_time`, `location_graph`, `open_orders`, `interchange_group`); required groups (`demand_history`, `vendor_economics`, `part_attributes`, `criticality`) propagate the error.

- [ ] **Step 1: Failing test** against a seeded `InMemoryFeatureStore` — read `part_attributes`, and assert a missing optional `open_orders` returns `None`.
- [ ] **Step 2: FAIL. Step 3: Implement.** Step 4: PASS. Step 5: Commit.

### Task 8: `ContextAssembler` (`data/assembler.py`)

**Interfaces:**
- Consumes: `FeatureReader`, `InventoryStateProvider`, `TenantPolicyConfig`.
- Produces: `ContextAssembler.assemble(*, tenant, pn, location) -> PartLocationContext`. Populates `description` from `part_attributes.description`. The lead-time vendor/condition is the preferred vendor from `vendor_economics` (fallback: first open order's vendor); if unknown, `lead_time=None`.

- [ ] **Step 1: Failing test**: seed both stores for one `(pn, location)`, assemble, assert `ctx.description == part_attributes.description` and every required field present.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

---

## Phase 3: Regime classification

### Task 9: `RegimeClassifier` (`regime/classifier.py`)

**Interfaces:**
- Produces: `classify(*, events_24mo: int, history_days: int, prior: Regime | None = None) -> Regime`. Thresholds (spec §6.1): `<6 ULTRA_RARE · 6–24 INTERMITTENT · 25–200 MODERATE · >200 HIGH_VOLUME`; `history_days < 90 → ULTRA_RARE`; ±20% hysteresis around a threshold keeps `prior` when within the band. Also `events_24mo_from(history: DemandHistory) -> int` summing `removals + issues`.

- [ ] **Step 1: Failing tests**: boundary cases (5→ULTRA, 6→INTERMITTENT, 200→MODERATE, 201→HIGH_VOLUME); hysteresis (prior=MODERATE, events=22 within 20% of 25 → stays MODERATE); new PN (history_days=30, events=50 → ULTRA_RARE).
- [ ] **Step 2: FAIL. Step 3: Implement.** Step 4: PASS. Step 5: Commit.

---

## Phase 4: Demand projection

### Task 10: `DemandProjector` (`demand/projection.py`)

**Interfaces:**
- Consumes: `PartLocationContext`, `Regime`.
- Produces: `DemandProjector` (`Protocol` `DemandProjectorProtocol` + concrete `HistoricalScheduledProjector`) with `project(*, context, regime) -> DemandProjection`. Computes a **per-day rate**: `historical_component = total_events / basis_window_days` (× causal scaler if `causal` present, × fleet-effectivity scaler), `scheduled_component = Σ scheduled_demand.qty within basis window / basis_window_days`, itemized into `by_aircraft` (keyed `ac_type`) and `by_task` (keyed `source_ref`). `dist_kind`/`dist_params` by regime: ULTRA_RARE/INTERMITTENT → `COMPOUND_POISSON` (`lambda = events/basis_days`, `clump_p` from mean demand-per-event), else `NORMAL` (`mean`,`var`). `mean_per_day = historical + scheduled`, `std_per_day` from the fitted distribution.

- [ ] **Step 1: Failing tests**: (a) intermittent history → `dist_kind=="COMPOUND_POISSON"` and `mean_per_day>0`; (b) scheduled items with two `ac_type`s populate `by_aircraft` with both; (c) high-volume history → `dist_kind=="NORMAL"`.
- [ ] **Step 2: FAIL. Step 3: Implement** (method-of-moments fits, deterministic, scipy only for distribution objects). Step 4: PASS. Step 5: Commit.

---

## Phase 5: Service-level math + LTD

### Task 11: Normal service-level primitives (`policy/service_level.py`)

**Interfaces:**
- Produces: `z_for_fill_rate(fill_rate: float) -> float` (= `norm.ppf`, raises on `fill_rate ∉ (0,1)`); `safety_stock_normal(*, sigma_ltd: float, service_level: float) -> float`.

- [ ] **Step 1: Failing tests** (textbook): `z_for_fill_rate(0.95) ≈ 1.6449`; `safety_stock_normal(sigma_ltd=20, service_level=0.95) ≈ 32.9`.
- [ ] **Step 2: FAIL. Step 3: Implement** (scipy `norm`). Step 4: PASS. Step 5: Commit.

### Task 12: LTD convolution (`policy/lead_time.py`)

**Interfaces:**
- Consumes: `DemandProjection`, lead-time mean/var.
- Produces: `ltd_normal(*, demand_mean_per_day, demand_var_per_day, lead_mean, lead_var) -> tuple[float, float]` (mean, sigma of lead-time-demand via the standard random-sum formula `mean=μ_d·μ_L`, `var=μ_L·σ_d² + μ_d²·σ_L²`); `ltd_pmf_compound_poisson(*, lam, clump_p, lead_mean, lead_var, support_max) -> list[float]` returning a PMF over `0..support_max` (numeric convolution — the slow path, implemented now per spec §6.4).

- [ ] **Step 1: Failing tests**: deterministic lead time (lead_var=0) reduces `ltd_normal` to `μ_d·μ_L`; the compound-Poisson PMF sums to ≈1 over a sufficient support.
- [ ] **Step 2: FAIL. Step 3: Implement.** Step 4: PASS. Step 5: Commit.

### Task 13: Quantile inversion (`policy/service_level.py` additions)

**Interfaces:**
- Produces: `ltd_quantile_from_pmf(pmf: list[float], p: float) -> int` (smallest `S` with `cumsum ≥ p`); used for base-stock `S` and intermittent `SS`.

- [ ] **Step 1: Failing test**: for a pmf where `P(LTD≤3)=0.96`, `ltd_quantile_from_pmf(pmf, 0.95) == 3`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

---

## Phase 6: Policy primitives + engine

### Task 14: Base-stock `(S-1,S)` (`policy/base_stock.py`)

**Interfaces:**
- Produces: `compute_base_stock(*, projection, lead_mean, lead_var, service_level) -> tuple[rop:int, eoq:int, ss:int, max_stock:int]` — `S = ltd_quantile`(target) over the compound-Poisson LTD PMF; returns `rop=S, eoq=1, ss=max(0, S - round(ltd_mean)), max_stock=S+1` (so `max ≥ rop+eoq` holds). Provide a normal-approx fallback when `dist_kind=="NORMAL"`.

- [ ] **Step 1: Failing test**: a pinned compound-Poisson case (λ, clump_p, lead) → known `S`. Assert floors (`rop≥ss`, `max≥rop+eoq`).
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

### Task 15: `(s,S)` continuous review (`policy/s_S.py`)

**Interfaces:**
- Produces: `compute_s_S(*, projection, lead_mean, lead_var, service_level, ordering_cost, holding_cost_rate, unit_cost, min_order_qty) -> tuple[...]` — `EOQ = max(min_order_qty, round(√(2·D·K/h)))` with `D` = annualized demand from `mean_per_day`, `h = holding_cost_rate·unit_cost`; `SS` from the LTD quantile (compound-Poisson tail); `ROP = round(ltd_mean) + SS`; `max_stock = ROP + EOQ`.

- [ ] **Step 1: Failing tests**: Wilson equivalence when LTD deterministic; `eoq ≥ min_order_qty`; floors hold.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

### Task 16: `(R,Q)` periodic review (`policy/R_Q.py`)

**Interfaces:**
- Produces: `compute_R_Q(*, projection, lead_mean, lead_var, service_level, ordering_cost, holding_cost_rate, unit_cost, min_order_qty, review_period_days) -> tuple[...]` — `SS = z·σ_LTD` (normal path); `ROP = round(ltd_mean over lead+review) + SS`; `Q = max(min_order_qty, EOQ)`; `max_stock = ROP + Q`.

- [ ] **Step 1: Failing tests**: floors; MinOQ floor on Q; SS monotonic in service level.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

### Task 17: Constraints (`policy/constraints.py`)

**Interfaces:**
- Consumes: a raw `(rop,eoq,ss,max)` tuple + `PartAttributes`, `CurrentPolicy`, `avg_daily_demand`, `min_order_qty`.
- Produces: `apply_constraints(...) -> ConstraintResult` where `ConstraintResult = (values | None, flags: list[str], violation: str | None)`. Tightening only: shelf-life clamp `max ≤ floor(0.6·shelf_life_days / max(avg_daily_demand, ε))`; hazmat/tool `max ≤ 2·current_max`; `eoq ≥ min_order_qty`; open-order deferral (caller supplies `available+receipts`; if `> proposed max`, flag `open_order_deferral` and return values unchanged). If a clamp makes `rop<ss` or `max<rop+eoq`, return `values=None, violation="..."` (caller routes to `skipped`).

- [ ] **Step 1: Failing tests**: shelf-life clamp reduces max; hazmat 2× cap; a clamp that breaks floors → `violation` set, `values None`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

### Task 18: `MiniPolicyEngine` (`policy/mini_engine.py`)

**Interfaces:**
- Consumes: Tasks 11–17 + `DemandProjection`, `Regime`, `CanonicalCriticality`, `TenantPolicyConfig`, lead-time inputs.
- Produces: `MiniPolicyEngine.recommend(*, context, regime, projection) -> PolicyRecommendation | PolicyConstraintViolation`. Dispatch (spec §6.2): `ULTRA_RARE → base_stock` (any tier — tier 1–2 explicitly, tier 3–5 routed to base-stock too); `INTERMITTENT → s_S`; else `R_Q`. Service-level target from `tenant_policy_config.service_level_by_tier[criticality]`. Applies constraints; on violation returns `PolicyConstraintViolation(reason=...)`; else builds `PolicyRecommendation(model_id="deterministic-v1", provenance_id=str(ULID()))`.

- [ ] **Step 1: Failing tests**: ultra-rare tier 1 → `policy_kind==BASE_STOCK`; ultra-rare tier 4 → `BASE_STOCK` (not R_Q); intermittent → `S_S`; high-volume → `R_Q`; a constraint-violating context → `PolicyConstraintViolation`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

---

## Phase 7: Net position

### Task 19: `NetPositionCalculator` (`position/net_position.py`)

**Interfaces:**
- Consumes: `PartLocationContext`, `DemandProjection`, `RepairTat`.
- Produces: `available(stock_position) -> float` = `max(0, serviceable - allocated_reserved)`; `expected_receipts(*, open_orders, repair_tat, stock_position, window_days, as_of) -> float` = `Σ open_orders.qty_open where expected_rcv_date ≤ as_of+window` + `Σ repair returns (in-repair units with projected RO-close ≤ window via repair_tat)`; `net_position(*, context, projection, window_days, as_of, group_rolled=False) -> NetPosition` with `projected_demand = mean_per_day·window` (NORMAL) and `net = available + receipts − projected_demand`, `shortage = max(0, −net)`. Group rollup: `rollup_group(contexts) -> aggregate` summing two-way members' demand (honoring one-way edges) and `apportion(values, members, trailing_consumption) -> dict[pn, values]` proportional to trailing-12-mo consumption.

- [ ] **Step 1: Failing tests**: `available` excludes in-repair/rental/loan; a receipt dated outside the window is excluded; net identity; no unit double-counted (assert helper that an in-repair unit is not also in open_orders); group rollup sums two-way members; apportionment proportional.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

---

## Phase 8: Recommenders

### Task 20: `Recommender` base (`recommenders/base.py`)

**Interfaces:**
- Produces: `Recommender` Protocol `propose(*, context, net_position_fn, regime) -> list[Recommendation]` where `net_position_fn(window_days, group_rolled=False) -> NetPosition`. Plus `make_evidence(...)` and `protection_period_days(context) -> float` helper (spec §6.5 precedence: `realized_mean_days if n_obs>0 else promised_lead_days else current_policy.replenishment_lead_days else 14`).

- [ ] **Step 1: Failing test** for `protection_period_days` precedence. Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.

### Task 21: `AdjustMinMaxRecommender` (`recommenders/adjust_min_max.py`)

- Runs `MiniPolicyEngine`; on `PolicyConstraintViolation` returns `[]` and records nothing (the service adds to `skipped`). Emits `ADJUST_MIN_MAX` iff any of `(rop,eoq,ss,max)` changes `>5%` (strict, relative) vs `current_policy`. Sets `guardrail_flags+=["delta_gt_100pct"]` when a single value delta `>100%`. `recommended_quantity = proposed.max_stock`. `horizon_days = round(protection_period)`. Evidence: `DEMAND_HISTORY`.
- [ ] TDD steps: failing test (material change → one ADJUST with `policy`/`current_policy` set; sub-5% change → no rec) → implement → pass → commit.

### Task 22: `PurchaseRecommender` (`recommenders/purchase.py`)

- `W = max(round(protection_period), reporting_horizon)`; `np = net_position_fn(W)`. Fire iff `np.net < 0`. `on_order = np.expected_receipts_in_window`; **suppress** iff open POs alone cover demand (`np.available + on_order ≥ projected_demand`). `buy_qty = ceil(np.shortage + safety_stock − on_order)` floored at `MinOQ`. `estimated_cost_impact = Decimal(buy_qty) * unit_cost`. Evidence: `OPEN_ORDER` + driving demand. `horizon_days=W`.
- [ ] TDD: scenarios 1 & 6 as unit tests (long-lead shortage → PURCHASE; open-PO-covers → none) → implement → pass → commit.

### Task 23: `TransferRecommender` (`recommenders/transfer.py`)

- Needs sibling-location stock. Consumes a `donor_lookup(pn, group, main_warehouse) -> list[(location, serviceable_excess, lead_days, cost)]` callback (the service supplies it from the work-list). Fire iff this location `shortage>0` and a valid **directed** donor exists with `serviceable − Max > 0`. `recommended_location = donor`, `recommended_quantity = min(shortage, donor_excess)`. Evidence: `DONOR_STOCK`.
- [ ] TDD: scenario 2 (donor exists, transfer cheaper → TRANSFER) + one-way edge direction respected → implement → pass → commit.

### Task 24: `ReduceSellRecommender` (`recommenders/reduce_sell.py`)

- Fire iff `serviceable > 1.5·Max` (strict) AND trailing usage over basis window `== 0` AND `unit_cost ≥ high_value_threshold`, OR shelf-life expiring. `SELL` iff usage`==0` AND no scheduled demand in horizon AND `unit_cost ≥ threshold`; else `REDUCE_STOCK`. `recommended_quantity = serviceable − Max`; `estimated_cost_impact = −Decimal(excess)·unit_cost·holding_cost_rate` (negative=savings). Evidence: zero-usage window, `SHELF_LIFE` where relevant.
- [ ] TDD: scenario 3 (high-value unused → REDUCE_STOCK or SELL per boundary) → implement → pass → commit.

---

## Phase 9: Arbitration

### Task 25: `Arbitrator` (`arbitration.py`)

**Interfaces:**
- Produces: `arbitrate(recs_for_key: list[Recommendation], *, net) -> list[Recommendation]`. Rules (spec §7.5): (1) if both TRANSFER and PURCHASE for the key — keep TRANSFER, recompute PURCHASE residual `= shortage − transfer_qty`; drop PURCHASE if residual ≤ 0 else lower its `recommended_quantity`. (2) if `net<0`, drop any `REDUCE_STOCK`/`SELL` for the key. Deterministic; preserves input order otherwise.

- [ ] **Step 1: Failing tests**: TRANSFER+PURCHASE on one key → PURCHASE residual-corrected or dropped; shortage key never keeps REDUCE/SELL; no-conflict passes through unchanged.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

---

## Phase 10: AOG risk

### Task 26: `AogRiskScorer` (`risk/aog.py`)

**Interfaces:**
- Produces: `score(rec: Recommendation, *, context, net) -> Recommendation` (returns a copy with `aog_risk_level`, possibly `suggested_autonomy_tier=ADVISOR`, `guardrail_flags+=["active_aog"]`, expedite text). `recovery_time = repair_tat.p90_days if part_class ∈ {rotable, repairable} and repair_tat.n_observations>0 else protection_period_days`. Risk from `criticality × shortage severity × recovery_time × recent AOG`; graceful degradation when stubs empty (never silent NONE for a real shortage on a critical part). Active AOG or `last_shortage_at` within 72h of `as_of` → ADVISOR + flag.

- [ ] **Step 1: Failing tests**: scenario 8 rotable long-TAT → `≥HIGH` + ADVISOR + expedite; expendable uses lead_days (counter-fixture); empty stub still yields a defined level for a critical shortage.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

---

## Phase 11: Confidence + ranking

### Task 27: `confidence` (`confidence.py`)

**Interfaces:**
- Produces: `confidence_score(*, context, regime, used_stub_inputs: set[str], constraint_bound: bool) -> float` in `[0,1]` = product of: demand sufficiency `min(1, events_24mo/regime_threshold)`, provenance `1.0 − 0.15·len(used_stub_inputs)` (clamped ≥0.1), constraint penalty (`0.85` if `constraint_bound`), regime-fit `0.9` if within hysteresis else `1.0`.

- [ ] **Step 1: Failing test**: same context, `used_stub_inputs={"aog","repair_tat"}` yields **strictly lower** score than `set()`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

### Task 28: `Ranker` + suggested tier (`ranking.py`)

**Interfaces:**
- Produces: `suggest_tier(*, criticality, unit_cost, delta_pct, active_aog) -> AutonomyTier` (spec §7.8 thresholds); `rank(recs: list[Recommendation]) -> list[Recommendation]` — sort key `(−score_decimal, criticality, part_number, type_order, current_location)` for a total order; `score` in `Decimal`.

- [ ] **Step 1: Failing tests**: tier thresholds (tier1→ADVISOR; cheap tier5 small delta→AUTONOMOUS; else BOUNDED); ranking is a total order with a deterministic tie-break (two equal-score recs ordered by part_number).
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

---

## Phase 12: Service facade

### Task 29: `RecommendationService` (`service.py`)

**Interfaces:**
- Consumes: everything above.
- Produces: `RecommendationService(feature_store, inventory_state, config).run(*, tenant, keys: list[tuple[str,str]], reporting_horizon_days: int = 30) -> RecommendationBatch`. Pipeline (spec §4.1): per key → assemble → classify regime → project demand → build `net_position_fn` → run 4 recommenders → arbitrate per key → AOG-score → confidence → collect; then rank all; build `summary`; collect `skipped` (constraint violations, missing required inputs). `input_snapshot_hash` = canonical sha256 of the assembled context (sorted keys, exclude volatile). `generated_at` is injected (param `now: datetime`) so tests are deterministic.

- [ ] **Step 1: Failing test**: a single seeded shortage key → batch with one PURCHASE, `summary.total==1`, deterministic `input_snapshot_hash`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

---

## Phase 13: CLI

### Task 30: `cli.py`

**Interfaces:**
- Produces: `main()` click group; `trax-io-reco run --tenant --keys-file --reporting-horizon --type` → loads a JSON keys file `[["PN","LOC"], ...]`, runs the service against a store built from a fixture loader (documented), prints `RecommendationBatch.model_dump_json(indent=2)`.

- [ ] **Step 1: Failing test** with click's `CliRunner` over a tiny seeded fixture → exit 0, JSON parses, contains `recommendations`.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit.**

---

## Phase 14: Optional API

### Task 31: `api/app.py` (behind `api` extra)

**Interfaces:**
- Produces: `create_app(service) -> FastAPI` with `GET /v1/recommendations` (query: tenant, location, type, min_confidence) and `GET /v1/recommendations/{pn}/{location}`. Returns `RecommendationBatch`/`Recommendation` JSON. The module imports FastAPI lazily inside `create_app` so core install (no `api` extra) still imports the package.

- [ ] **Step 1: Failing test** guarded by `pytest.importorskip("fastapi")` using `TestClient` → `/v1/recommendations?tenant=t` returns 200 with a batch.
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS (skips cleanly without fastapi). Step 5: Commit.**

---

## Phase 15: Acceptance + invariants

### Task 32: Scenario fixtures (`tests/fixtures/scenarios.py`)

**Interfaces:**
- Produces: eight builder functions `scenario_demand_exceeds_stock() -> tuple[InMemoryFeatureStore, InMemoryInventoryState, key]`, ... one per spec §9.1 row. Each seeds the real `InMemoryFeatureStore` (via `.seed(tenant_id, bucket, key_tuple, model)` — key tuples per recon: `part_attributes→(pn,)`, `open_orders_snapshot→(pn,location)`, etc.) + `InMemoryInventoryState`.

- [ ] **Step 1:** Write the 8 builders (no test yet — fixtures). Step 2: Commit.

### Task 33: Eight acceptance scenarios (`tests/test_eight_scenarios.py`)

- [ ] One test per scenario, asserting exactly the spec §9.1 outcome table (single deterministic outcome each; scenario 6 asserts **no** PURCHASE type present; every test asserts `description` non-empty). TDD: write all 8 (failing) → run → fix any engine gaps surfaced → all pass → commit.

### Task 34: Invariants + contract pin (`tests/test_invariants.py`, finalize `tests/test_contracts.py`)

- [ ] Property/invariant tests (spec §9.3): net-position identity; `shortage≥0`; non-empty `description`/`reason`/`evidence`; `confidence∈[0,1]`; ranking total order; **determinism** (run a batch twice → identical modulo ids); no `(REDUCE_STOCK|SELL)` with `(PURCHASE|TRANSFER)` on one key. Finalize `test_contracts.py` mirror pins. TDD → pass → commit.

---

## Phase 16: Docs + project trackers

### Task 35: README + run/test docs

- [ ] `services/recommendation-engine/README.md`: what it is, `uv sync --extra dev`, `uv run --extra dev pytest`, the CLI example, the optional `api` extra, the deferred-stub table (spec §10), and the promotion path. Commit.

### Task 36: Update CLAUDE.md / ROADMAP / TASKS

- [ ] Add run/test commands for `services/recommendation-engine/` to CLAUDE.md Section A. Tick the ROADMAP sub-project items. Update TASKS.md "Completed This Session" + "Next Session". Commit.

---

## Self-Review

| Spec section | Covered by |
|---|---|
| §3.1 governance (ADR-0004 + roadmap) | Task 0 |
| §5.1 enums + policy mirror (named ForecastHorizon, validator, model_id default) | Tasks 2–3, 34 |
| §5.2 recommendation contracts | Task 5 |
| §5.3 context (gap models + computed + reuse FS schemas) | Task 4 |
| §5.4 9 FS reads | Task 7 |
| §5.5 available formula + receipts disjointness | Task 19 |
| §6.1 regime classifier + hysteresis | Task 9 |
| §6.2 dispatch incl. ultra-rare tier 3–5 → base-stock | Task 18 |
| §6.3 constraints + violation→skipped flow | Tasks 17, 29 |
| §6.4 distribution params + numeric quantile path | Tasks 10, 12, 13, 14 |
| §6.5 lead-time precedence | Task 20 |
| §7.1–7.4 four recommenders | Tasks 21–24 |
| §7.5 arbitration | Task 25 |
| §7.6 interchange rollup/apportion + one-way | Task 19 |
| §7.7 AOG scorer (part-class recovery time) | Task 26 |
| §7.8 suggested tier thresholds | Task 28 |
| §7.9 confidence formula + ranking determinism + canonical hash | Tasks 27, 28, 29 |
| §8 library/CLI/optional API | Tasks 29–31 |
| §9.1 eight scenarios | Tasks 32–33 |
| §9.3 invariants + determinism | Task 34 |
| §10 stub provider + promotion paths | Tasks 6, 35 |

**Estimated effort:** ~1 engineer, ~2 weeks. Phase 5–6 (math) and Phase 7 (net position) are the load-bearing core — front-load onto the strongest engineer. Phases 13–14 (CLI/API) and 16 (docs) are mechanical.
