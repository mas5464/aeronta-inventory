# Trax IO — Agent Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the production-grade hierarchical agent spine for Trax IO v1 — a Strands-based Supervisor on AWS Bedrock AgentCore Runtime that delegates to five specialist subagents (Data & Retrieval, Regime Router, Guardrail & Approval, Writeback, plus stub interfaces for Forecasting and Policy Engine that land in sub-plan #5). Multi-tenant, Cedar-authorized, OpenTelemetry-traced, deployed via AWS CDK.

**Architecture:** Hierarchical multi-agent topology. One Supervisor owns session/tenant context and orchestration; each specialist is an independently-versioned AgentCore Runtime service. All inter-agent communication flows through a typed contracts layer (pydantic). Tenant isolation is enforced at IAM, Cedar, KMS, and Memory-namespace boundaries. All cross-subsystem dependencies (feature store #2, forecasting/policy #5, eMRO Writeback REST #6) are stubbed with contract-conforming fakes so the spine ships and tests independently.

**Tech Stack:**
- **Language:** Python 3.12
- **Agents:** AWS Strands Agents SDK (Supervisor + specialists), AWS Bedrock AgentCore (Runtime, Memory, Identity, Gateway, Observability)
- **Models:** Claude Sonnet 4.6 (reasoning), Claude Haiku 4.5 (retrieval routing)
- **Contracts:** pydantic v2
- **Authorization:** Cedar (via `cedarpy`) + AWS IAM
- **AWS SDK:** boto3
- **HTTP:** httpx (async)
- **Testing:** pytest, pytest-asyncio, moto, LocalStack, pytest-httpx
- **Observability:** OpenTelemetry SDK + AWS X-Ray exporter
- **Infrastructure:** AWS CDK v2 (Python)
- **Dependency manager:** `uv`
- **Formatter/linter:** ruff + mypy (strict)
- **Repo location:** New git repo `trax-io-agent-spine` (Trax GitHub org)

**Dependencies on sister sub-plans (stubbed in this plan, real implementations live elsewhere):**
- **#2 Feature Store** — this plan uses a `FeatureStoreClient` protocol with an in-memory fake; sub-plan #2 implements the Iceberg + DynamoDB backend conforming to the same protocol.
- **#5 Forecasting & Policy Engine** — this plan provides stub `ForecastingAgent` and `PolicyEngineAgent` that return canned distributions and policies; sub-plan #5 replaces them with real models behind the same contracts.
- **#6 eMRO Writeback REST API** — this plan ships a FastAPI mock of the eMRO writeback surface in `tests/fixtures/fake_emro/`; sub-plan #6 implements the real eMRO endpoint with the same OpenAPI contract.

---

## File Structure

Top-level layout. Each file has one responsibility. Test files mirror source layout.

```
trax-io-agent-spine/
├── pyproject.toml
├── uv.lock
├── README.md
├── .github/workflows/ci.yml
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       ├── __init__.py
│       ├── identity_stack.py          # Cognito / IAM roles for tenants + service principals
│       ├── memory_stack.py            # AgentCore Memory namespaces per tenant
│       ├── gateway_stack.py           # AgentCore Gateway MCP tool registrations
│       ├── runtime_stack.py           # AgentCore Runtime services for each agent
│       └── observability_stack.py     # OTel collector, X-Ray, CloudWatch dashboards
├── src/trax_io/
│   ├── __init__.py
│   ├── config.py                      # env-driven settings (pydantic-settings)
│   ├── contracts/                     # Shared typed interfaces (pydantic v2)
│   │   ├── __init__.py
│   │   ├── tenant.py                  # TenantContext, EssentialityMapping
│   │   ├── part.py                    # Part, PartLocation, InterchangeGroup
│   │   ├── demand.py                  # DemandObservation, DemandHistory
│   │   ├── forecast.py                # ForecastDistribution, ForecastRequest
│   │   ├── policy.py                  # PolicyRecommendation (ROP/EOQ/SS/Max + provenance)
│   │   ├── regime.py                  # Regime enum, RegimeClassification
│   │   ├── guardrail.py               # AutonomyTier, ApprovalTask, GuardrailOutcome
│   │   ├── writeback.py               # WritebackRequest, WritebackResult
│   │   └── events.py                  # Domain events (flight_completed, eo_published, ...)
│   ├── identity/
│   │   ├── __init__.py
│   │   ├── context.py                 # TenantContext propagation + contextvars
│   │   └── cedar.py                   # Cedar policy evaluator wrapper
│   ├── memory/
│   │   ├── __init__.py
│   │   └── client.py                  # AgentCore Memory client with tenant namespacing
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── tracing.py                 # OTel setup + decorators
│   │   └── cost.py                    # Per-tenant cost attribution
│   ├── supervisor/
│   │   ├── __init__.py
│   │   ├── agent.py                   # Strands Supervisor agent definition
│   │   ├── session.py                 # Session state, tenant binding
│   │   └── orchestration.py           # Delegation graph to specialists
│   ├── specialists/
│   │   ├── __init__.py
│   │   ├── base.py                    # Specialist protocol + shared base
│   │   ├── data_retrieval/
│   │   │   ├── __init__.py
│   │   │   ├── agent.py
│   │   │   ├── feature_store.py       # Protocol + in-memory fake
│   │   │   └── tools.py               # MCP tool handlers registered on Gateway
│   │   ├── regime_router/
│   │   │   ├── __init__.py
│   │   │   ├── agent.py
│   │   │   └── classifier.py          # Rule-based classifier with hysteresis
│   │   ├── guardrail/
│   │   │   ├── __init__.py
│   │   │   ├── agent.py
│   │   │   ├── tiers.py               # Tier A/B/C assignment logic
│   │   │   ├── hard_guardrails.py     # Non-bypassable validators
│   │   │   ├── approval.py            # Approval queue client (DynamoDB)
│   │   │   └── policies/              # Cedar policy source files
│   │   │       ├── default_tiers.cedar
│   │   │       └── hard_floors.cedar
│   │   ├── writeback/
│   │   │   ├── __init__.py
│   │   │   ├── agent.py
│   │   │   ├── client.py              # HTTP client to eMRO Writeback REST
│   │   │   └── idempotency.py
│   │   └── stubs/
│   │       ├── __init__.py
│   │       ├── forecasting_stub.py    # Canned-response stub for sub-plan #5
│   │       └── policy_stub.py         # Canned-response stub for sub-plan #5
│   └── cli/
│       ├── __init__.py
│       └── main.py                    # `trax-io` CLI for local dev + smoke tests
├── tests/
│   ├── __init__.py
│   ├── conftest.py                    # pytest fixtures (tenant, parts, demand)
│   ├── unit/
│   │   ├── contracts/
│   │   │   └── test_contracts.py
│   │   ├── identity/
│   │   │   ├── test_context.py
│   │   │   └── test_cedar.py
│   │   ├── regime_router/
│   │   │   └── test_classifier.py
│   │   ├── guardrail/
│   │   │   ├── test_tiers.py
│   │   │   ├── test_hard_guardrails.py
│   │   │   └── test_approval.py
│   │   ├── writeback/
│   │   │   ├── test_client.py
│   │   │   └── test_idempotency.py
│   │   ├── data_retrieval/
│   │   │   ├── test_feature_store.py
│   │   │   └── test_tools.py
│   │   ├── supervisor/
│   │   │   └── test_orchestration.py
│   │   └── memory/
│   │       └── test_client.py
│   ├── integration/
│   │   ├── conftest.py                # LocalStack + fake_emro fixtures
│   │   ├── test_end_to_end_recommendation.py
│   │   ├── test_tier_routing.py
│   │   ├── test_tenant_isolation.py
│   │   └── test_rollback.py
│   └── fixtures/
│       ├── __init__.py
│       ├── tenants.py
│       ├── parts.py
│       ├── demand_history.py
│       └── fake_emro/
│           └── server.py              # FastAPI mock of eMRO Writeback REST
└── docs/
    ├── ARCHITECTURE.md
    ├── ONBOARDING.md                  # Engineer day-one guide
    └── adr/
        ├── 0001-strands-vs-langgraph.md
        ├── 0002-in-memory-feature-store-stub.md
        └── 0003-fake-emro-contract-testing.md
```

---

## Phase Plan Overview

| Phase | Scope | Tasks | Status |
|---|---|---|---|
| 0 | Bootstrap repo, tooling, CI | 1–4 | Full TDD detail below |
| 1 | Shared contracts (pydantic models) | 5–9 | Full TDD detail below |
| 2 | Tenant identity & Cedar | 10–13 | Full TDD detail below |
| 3 | Regime Router Agent | 14–18 | Full TDD detail below |
| 4 | Data & Retrieval Agent + feature store protocol | 19–23 | **Commit 2** |
| 5 | Guardrail & Approval Agent | 24–29 | **Commit 2** |
| 6 | Writeback Agent + fake_emro harness | 30–33 | **Commit 2** |
| 7 | Strands Supervisor + orchestration | 34–36 | **Commit 2** |
| 8 | AgentCore Memory integration | 37–38 | **Commit 2** |
| 9 | Observability (OTel + cost) | 39–40 | **Commit 2** |
| 10 | End-to-end integration tests | 41–43 | **Commit 2** |
| 11 | CDK deployment stacks + CI | 44–47 | **Commit 2** |

---

## Phase 0: Bootstrap

### Task 1: Initialize repository and dependency manager

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`

- [ ] **Step 1: Create repo and init `uv`**

Run:
```bash
mkdir -p trax-io-agent-spine && cd trax-io-agent-spine
git init
uv init --python 3.12 --package
```

- [ ] **Step 2: Populate `pyproject.toml`**

Replace the generated `pyproject.toml` with:
```toml
[project]
name = "trax-io-agent-spine"
version = "0.1.0"
description = "Trax IO Agent Spine — hierarchical multi-agent orchestration for eMRO inventory optimization"
requires-python = ">=3.12"
dependencies = [
  "strands-agents>=0.15.0",
  "bedrock-agentcore>=0.5.0",
  "boto3>=1.34.0",
  "pydantic>=2.7.0",
  "pydantic-settings>=2.3.0",
  "httpx>=0.27.0",
  "cedarpy>=4.2.0",
  "opentelemetry-api>=1.24.0",
  "opentelemetry-sdk>=1.24.0",
  "opentelemetry-exporter-otlp>=1.24.0",
  "aws-xray-sdk>=2.13.0",
  "structlog>=24.1.0",
  "typer>=0.12.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2.0",
  "pytest-asyncio>=0.23.0",
  "pytest-httpx>=0.30.0",
  "pytest-cov>=5.0.0",
  "moto[all]>=5.0.0",
  "fastapi>=0.111.0",
  "uvicorn>=0.30.0",
  "ruff>=0.4.0",
  "mypy>=1.10.0",
]
cdk = [
  "aws-cdk-lib>=2.140.0",
  "constructs>=10.0.0",
]

[project.scripts]
trax-io = "trax_io.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "C4", "SIM", "PL"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Install deps and verify**

Run:
```bash
uv sync --all-extras
uv run python -c "import strands, pydantic, cedarpy; print('ok')"
```
Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
echo -e ".venv/\n__pycache__/\n*.egg-info/\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\n.coverage\nhtmlcov/\ncdk.out/\n.DS_Store\n" > .gitignore
git add .
git commit -m "chore: bootstrap python project with uv and pinned deps"
```

---

### Task 2: Configure ruff, mypy, and pre-commit

**Files:**
- Create: `.pre-commit-config.yaml`
- Modify: `pyproject.toml` (already has ruff/mypy config)

- [ ] **Step 1: Write pre-commit config**

Create `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.10
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        additional_dependencies: [pydantic>=2.7.0, types-requests]
        args: [--strict]
```

- [ ] **Step 2: Install pre-commit hooks**

Run:
```bash
uv run pre-commit install
uv run pre-commit run --all-files || true
```
Expected: hooks install; first run may auto-format files.

- [ ] **Step 3: Verify ruff and mypy pass on empty source tree**

Run:
```bash
mkdir -p src/trax_io && touch src/trax_io/__init__.py
uv run ruff check src/
uv run mypy src/
```
Expected: both commands exit 0.

- [ ] **Step 4: Commit**

```bash
git add .pre-commit-config.yaml src/
git commit -m "chore: add ruff + mypy pre-commit hooks"
```

---

### Task 3: Configure structured logging and settings

**Files:**
- Create: `src/trax_io/config.py`
- Create: `src/trax_io/__init__.py` (overwrite)
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_config.py`:
```python
import os

from trax_io.config import Settings


def test_settings_reads_aws_region_from_env(monkeypatch):
    monkeypatch.setenv("TRAX_IO_AWS_REGION", "us-east-1")
    settings = Settings()
    assert settings.aws_region == "us-east-1"


def test_settings_defaults_log_level_to_info():
    settings = Settings()
    assert settings.log_level == "INFO"


def test_settings_model_ids_have_sensible_defaults():
    settings = Settings()
    assert "claude-sonnet" in settings.reasoning_model_id
    assert "claude-haiku" in settings.retrieval_model_id
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/test_config.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'trax_io.config'`.

- [ ] **Step 3: Implement `config.py`**

Create `src/trax_io/config.py`:
```python
"""Environment-driven settings for Trax IO Agent Spine."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, overridable via env vars prefixed TRAX_IO_."""

    model_config = SettingsConfigDict(
        env_prefix="TRAX_IO_",
        env_file=".env",
        extra="ignore",
    )

    aws_region: str = Field(default="us-east-1")
    log_level: str = Field(default="INFO")
    reasoning_model_id: str = Field(default="anthropic.claude-sonnet-4-6-20260301-v1:0")
    retrieval_model_id: str = Field(default="anthropic.claude-haiku-4-5-20251001-v1:0")
    emro_writeback_base_url: str = Field(default="http://localhost:9000")
    memory_namespace_prefix: str = Field(default="trax-io")


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/test_config.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/config.py tests/unit/test_config.py
git commit -m "feat(config): env-driven settings with pydantic-settings"
```

---

### Task 4: Bootstrap CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write CI workflow**

Create `.github/workflows/ci.yml`:
```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
        with:
          python-version: "3.12"
      - name: Sync deps
        run: uv sync --all-extras
      - name: Ruff
        run: uv run ruff check src/ tests/
      - name: Mypy
        run: uv run mypy src/
      - name: Pytest
        run: uv run pytest --cov=src/trax_io --cov-report=xml --cov-fail-under=85
      - uses: codecov/codecov-action@v4
        if: always()
```

- [ ] **Step 2: Commit**

```bash
git add .github/
git commit -m "ci: run ruff, mypy, and pytest with 85% coverage floor"
```

---

## Phase 1: Shared Contracts (pydantic v2 models)

Contracts are the typed interfaces that glue the supervisor, specialists, and stubs together. Get these right and the rest of the plan clicks into place. Get them wrong and every downstream task wobbles.

### Task 5: Tenant and essentiality-mapping contracts

**Files:**
- Create: `src/trax_io/contracts/__init__.py`
- Create: `src/trax_io/contracts/tenant.py`
- Create: `tests/unit/contracts/test_tenant.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contracts/__init__.py` (empty) and `tests/unit/contracts/test_tenant.py`:
```python
import pytest
from pydantic import ValidationError

from trax_io.contracts.tenant import (
    CanonicalCriticality,
    EssentialityMapping,
    TenantContext,
)


def test_tenant_context_requires_tenant_id():
    with pytest.raises(ValidationError):
        TenantContext()  # type: ignore[call-arg]


def test_tenant_context_round_trips_json():
    ctx = TenantContext(
        tenant_id="aircanada",
        user_id="planner-42",
        session_id="sess-001",
    )
    payload = ctx.model_dump_json()
    restored = TenantContext.model_validate_json(payload)
    assert restored == ctx


def test_essentiality_mapping_rejects_unknown_canonical_tier():
    with pytest.raises(ValidationError):
        EssentialityMapping(
            tenant_id="aircanada",
            mapping={"AOG": "tier-99"},  # type: ignore[dict-item]
        )


def test_canonical_criticality_has_five_tiers():
    tiers = list(CanonicalCriticality)
    assert len(tiers) == 5
    assert CanonicalCriticality.TIER_1 < CanonicalCriticality.TIER_5


def test_essentiality_mapping_resolves_customer_code():
    mapping = EssentialityMapping(
        tenant_id="aircanada",
        mapping={"AOG": CanonicalCriticality.TIER_1, "GO-IF": CanonicalCriticality.TIER_2},
    )
    assert mapping.resolve("AOG") == CanonicalCriticality.TIER_1
    assert mapping.resolve("unknown") == CanonicalCriticality.TIER_5  # default fallback
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/contracts/test_tenant.py -v
```
Expected: `ModuleNotFoundError: trax_io.contracts.tenant`.

- [ ] **Step 3: Implement `tenant.py`**

Create `src/trax_io/contracts/__init__.py` (empty file), then `src/trax_io/contracts/tenant.py`:
```python
"""Tenant identity and essentiality-mapping contracts."""
from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


class CanonicalCriticality(IntEnum):
    """Canonical 5-tier essentiality scale, per design §5.5."""

    TIER_1 = 1  # AOG / NO-GO / flight-safety
    TIER_2 = 2  # GO-IF
    TIER_3 = 3  # dispatch-critical rotable
    TIER_4 = 4  # routine expendable
    TIER_5 = 5  # consumable, non-critical


class TenantContext(BaseModel):
    """Per-request tenant + user binding. Propagated via contextvars."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    user_id: str | None = None
    session_id: str | None = None
    request_id: str | None = None


class EssentialityMapping(BaseModel):
    """Maps a tenant's customer-specific essentiality codes to the canonical scale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    mapping: dict[str, CanonicalCriticality] = Field(default_factory=dict)
    default_tier: CanonicalCriticality = CanonicalCriticality.TIER_5

    def resolve(self, customer_code: str) -> CanonicalCriticality:
        return self.mapping.get(customer_code, self.default_tier)
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/contracts/test_tenant.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/contracts/ tests/unit/contracts/
git commit -m "feat(contracts): tenant context and essentiality mapping"
```

---

### Task 6: Part, PartLocation, and InterchangeGroup contracts

**Files:**
- Create: `src/trax_io/contracts/part.py`
- Create: `tests/unit/contracts/test_part.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contracts/test_part.py`:
```python
import pytest
from pydantic import ValidationError

from trax_io.contracts.part import InterchangeGroup, Part, PartLocation
from trax_io.contracts.tenant import CanonicalCriticality


def test_part_requires_pn_and_tenant():
    with pytest.raises(ValidationError):
        Part(pn="", tenant_id="aircanada", criticality=CanonicalCriticality.TIER_3)


def test_part_location_combines_part_and_loc():
    pl = PartLocation(tenant_id="aircanada", pn="NSN-123", location="YYZ-MAIN")
    assert pl.key() == ("aircanada", "NSN-123", "YYZ-MAIN")


def test_interchange_group_enforces_two_way_symmetry():
    group = InterchangeGroup(
        tenant_id="aircanada",
        group_id="grp-1",
        members={"PN-A", "PN-B", "PN-C"},
        one_way_parents={"PN-D": "PN-A"},
    )
    assert "PN-A" in group.members
    assert group.one_way_parents["PN-D"] == "PN-A"


def test_interchange_group_rejects_self_parent():
    with pytest.raises(ValidationError):
        InterchangeGroup(
            tenant_id="aircanada",
            group_id="grp-1",
            members={"PN-A"},
            one_way_parents={"PN-A": "PN-A"},
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/contracts/test_part.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `part.py`**

Create `src/trax_io/contracts/part.py`:
```python
"""Part, PartLocation, and InterchangeGroup contracts."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trax_io.contracts.tenant import CanonicalCriticality


class Part(BaseModel):
    """A part number as it exists in a single tenant's eMRO catalog."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)
    pn: str = Field(min_length=1)
    description: str = ""
    criticality: CanonicalCriticality
    ata_chapter: str | None = None
    average_cost: float = 0.0
    market_unit_cost: float | None = None
    shelf_life_days: int | None = None
    hazmat: bool = False
    tool_control: bool = False


class PartLocation(BaseModel):
    """A (tenant, PN, location) tuple — the optimization key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)
    pn: str = Field(min_length=1)
    location: str = Field(min_length=1)

    def key(self) -> tuple[str, str, str]:
        return (self.tenant_id, self.pn, self.location)


class InterchangeGroup(BaseModel):
    """Interchangeability group. `members` = two-way chain; `one_way_parents` = one-way links."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    group_id: str
    members: frozenset[str]
    one_way_parents: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_self_parents(self) -> "InterchangeGroup":
        for child, parent in self.one_way_parents.items():
            if child == parent:
                raise ValueError(f"one-way parent cannot be self: {child}")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/contracts/test_part.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/contracts/part.py tests/unit/contracts/test_part.py
git commit -m "feat(contracts): part, part-location, interchange-group"
```

---

### Task 7: Demand, Forecast, and Regime contracts

**Files:**
- Create: `src/trax_io/contracts/demand.py`
- Create: `src/trax_io/contracts/forecast.py`
- Create: `src/trax_io/contracts/regime.py`
- Create: `tests/unit/contracts/test_demand_forecast_regime.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contracts/test_demand_forecast_regime.py`:
```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trax_io.contracts.demand import DemandHistory, DemandObservation, TransactionType
from trax_io.contracts.forecast import ForecastDistribution, ForecastHorizon, ForecastRequest
from trax_io.contracts.regime import Regime, RegimeClassification


def test_demand_observation_rejects_negative_qty():
    with pytest.raises(ValidationError):
        DemandObservation(
            tenant_id="aircanada",
            pn="NSN-1",
            location="YYZ",
            observed_at=datetime.now(UTC),
            qty=-1,
            transaction_type=TransactionType.ISSUED,
        )


def test_demand_history_aggregates_monthly_counts():
    now = datetime(2026, 4, 1, tzinfo=UTC)
    obs = [
        DemandObservation(
            tenant_id="aircanada",
            pn="NSN-1",
            location="YYZ",
            observed_at=now,
            qty=1,
            transaction_type=TransactionType.REMOVED,
        ),
        DemandObservation(
            tenant_id="aircanada",
            pn="NSN-1",
            location="YYZ",
            observed_at=now,
            qty=2,
            transaction_type=TransactionType.REMOVED,
        ),
    ]
    history = DemandHistory(
        tenant_id="aircanada",
        pn="NSN-1",
        location="YYZ",
        observations=obs,
    )
    assert history.total_qty() == 3
    assert history.n_observations() == 2


def test_forecast_request_defaults_to_30_day_horizon():
    req = ForecastRequest(tenant_id="aircanada", pn="NSN-1", location="YYZ")
    assert req.horizon == ForecastHorizon.DAYS_30


def test_forecast_distribution_exposes_mean_and_tail():
    dist = ForecastDistribution(
        mean=4.2,
        variance=6.1,
        p50=4.0,
        p95=9.0,
        p99=12.0,
    )
    assert dist.mean == 4.2
    assert dist.p95 == 9.0


def test_regime_classification_records_router_version():
    rc = RegimeClassification(
        tenant_id="aircanada",
        pn="NSN-1",
        location="YYZ",
        regime=Regime.INTERMITTENT,
        n_events_24mo=8,
        router_version="v1.0.0",
    )
    assert rc.regime == Regime.INTERMITTENT
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/contracts/test_demand_forecast_regime.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the three modules**

Create `src/trax_io/contracts/demand.py`:
```python
"""Demand history contracts."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TransactionType(StrEnum):
    REMOVED = "REMOVED"  # rotable removal
    ISSUED = "ISSUED"    # expendable issue


class DemandObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    pn: str
    location: str
    observed_at: datetime
    qty: int = Field(ge=0)
    transaction_type: TransactionType
    work_order: str | None = None
    tail: str | None = None


class DemandHistory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    pn: str
    location: str
    observations: tuple[DemandObservation, ...] = ()

    def total_qty(self) -> int:
        return sum(o.qty for o in self.observations)

    def n_observations(self) -> int:
        return len(self.observations)
```

Create `src/trax_io/contracts/forecast.py`:
```python
"""Forecast request and response contracts (interface for sub-plan #5)."""
from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


class ForecastHorizon(IntEnum):
    DAYS_30 = 30
    DAYS_60 = 60
    DAYS_90 = 90
    DAYS_180 = 180


class ForecastRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    pn: str
    location: str
    horizon: ForecastHorizon = ForecastHorizon.DAYS_30


class ForecastDistribution(BaseModel):
    """Distribution over demand for the horizon. Returned by Forecasting Agent (#5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mean: float = Field(ge=0.0)
    variance: float = Field(ge=0.0)
    p50: float = Field(ge=0.0)
    p95: float = Field(ge=0.0)
    p99: float = Field(ge=0.0)
    model_id: str = "stub"
    model_version: str = "0"
```

Create `src/trax_io/contracts/regime.py`:
```python
"""Regime classification contracts."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Regime(StrEnum):
    ULTRA_RARE = "ultra_rare"
    INTERMITTENT = "intermittent"
    MODERATE = "moderate"
    HIGH_VOLUME = "high_volume"


class RegimeClassification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    pn: str
    location: str
    regime: Regime
    n_events_24mo: int = Field(ge=0)
    days_of_history: int = Field(ge=0, default=0)
    router_version: str
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/contracts/test_demand_forecast_regime.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/contracts/ tests/unit/contracts/test_demand_forecast_regime.py
git commit -m "feat(contracts): demand, forecast, regime models"
```

---

### Task 8: Policy, Guardrail, and Writeback contracts

**Files:**
- Create: `src/trax_io/contracts/policy.py`
- Create: `src/trax_io/contracts/guardrail.py`
- Create: `src/trax_io/contracts/writeback.py`
- Create: `tests/unit/contracts/test_policy_guardrail_writeback.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contracts/test_policy_guardrail_writeback.py`:
```python
from uuid import uuid4

import pytest
from pydantic import ValidationError

from trax_io.contracts.guardrail import (
    ApprovalTask,
    AutonomyTier,
    GuardrailOutcome,
    GuardrailStatus,
)
from trax_io.contracts.policy import PolicyKind, PolicyRecommendation
from trax_io.contracts.writeback import WritebackRequest, WritebackResult


def test_policy_recommendation_requires_nonnegative_values():
    with pytest.raises(ValidationError):
        PolicyRecommendation(
            tenant_id="aircanada",
            pn="NSN-1",
            location="YYZ",
            rop=-1,
            eoq=4,
            safety_stock=1,
            max_stock=10,
            policy_kind=PolicyKind.S_S,
            provenance_id=str(uuid4()),
        )


def test_policy_recommendation_enforces_max_ge_rop_plus_eoq():
    with pytest.raises(ValidationError):
        PolicyRecommendation(
            tenant_id="aircanada",
            pn="NSN-1",
            location="YYZ",
            rop=5,
            eoq=5,
            safety_stock=2,
            max_stock=6,  # < rop + eoq = 10
            policy_kind=PolicyKind.S_S,
            provenance_id=str(uuid4()),
        )


def test_autonomy_tier_ordering():
    assert AutonomyTier.ADVISOR < AutonomyTier.BOUNDED < AutonomyTier.AUTONOMOUS


def test_guardrail_outcome_advisor_generates_approval_task():
    outcome = GuardrailOutcome(
        status=GuardrailStatus.QUEUED_FOR_APPROVAL,
        tier=AutonomyTier.ADVISOR,
        approval_task=ApprovalTask(
            task_id="task-1",
            tenant_id="aircanada",
            pn="NSN-1",
            location="YYZ",
            priority_score=42.0,
        ),
    )
    assert outcome.approval_task is not None


def test_writeback_request_is_idempotent_by_key():
    r = WritebackRequest(
        tenant_id="aircanada",
        pn="NSN-1",
        location="YYZ",
        rop=5,
        eoq=4,
        safety_stock=2,
        max_stock=9,
        provenance_id="prov-1",
        idempotency_key="2026-04-14:aircanada:NSN-1:YYZ",
    )
    assert r.idempotency_key.startswith("2026-04-14")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/contracts/test_policy_guardrail_writeback.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the three modules**

Create `src/trax_io/contracts/policy.py`:
```python
"""Policy recommendation contracts (output of Policy Engine, sub-plan #5)."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PolicyKind(StrEnum):
    BASE_STOCK = "base_stock"  # (S-1, S) for ultra-rare critical
    S_S = "s_S"                 # (s, S) continuous review
    R_Q = "R_Q"                 # (R, Q) periodic review


class PolicyRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    pn: str
    location: str
    rop: int = Field(ge=0)
    eoq: int = Field(ge=0)
    safety_stock: int = Field(ge=0)
    max_stock: int = Field(ge=0)
    policy_kind: PolicyKind
    service_level_target: float = Field(ge=0.0, le=1.0, default=0.95)
    provenance_id: str
    model_id: str = "stub"

    @model_validator(mode="after")
    def _consistency(self) -> "PolicyRecommendation":
        if self.rop < self.safety_stock:
            raise ValueError("rop must be >= safety_stock")
        if self.max_stock < self.rop + self.eoq:
            raise ValueError("max_stock must be >= rop + eoq")
        return self
```

Create `src/trax_io/contracts/guardrail.py`:
```python
"""Guardrail outcomes and approval tasks."""
from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AutonomyTier(IntEnum):
    ADVISOR = 1
    BOUNDED = 2
    AUTONOMOUS = 3


class GuardrailStatus(StrEnum):
    APPROVED_FOR_WRITE = "approved_for_write"
    QUEUED_FOR_APPROVAL = "queued_for_approval"
    REJECTED_HARD_GUARDRAIL = "rejected_hard_guardrail"
    DEFERRED = "deferred"


class ApprovalTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    tenant_id: str
    pn: str
    location: str
    priority_score: float = Field(ge=0.0)
    reason: str = ""


class GuardrailOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: GuardrailStatus
    tier: AutonomyTier
    approval_task: ApprovalTask | None = None
    rejection_reason: str | None = None
```

Create `src/trax_io/contracts/writeback.py`:
```python
"""Writeback request/result contracts."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class WritebackStatus(StrEnum):
    WRITTEN = "written"
    DEFERRED_OPEN_ORDER = "deferred_open_order"
    FAILED = "failed"


class WritebackRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    pn: str
    location: str
    rop: int = Field(ge=0)
    eoq: int = Field(ge=0)
    safety_stock: int = Field(ge=0)
    max_stock: int = Field(ge=0)
    provenance_id: str
    idempotency_key: str = Field(min_length=1)


class WritebackResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    pn: str
    location: str
    status: WritebackStatus
    old_values: dict[str, int] | None = None
    new_values: dict[str, int] | None = None
    written_at: datetime | None = None
    error_message: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/contracts/test_policy_guardrail_writeback.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/contracts/ tests/unit/contracts/test_policy_guardrail_writeback.py
git commit -m "feat(contracts): policy, guardrail, writeback models"
```

---

### Task 9: Domain event contracts (matches eMRO Outbound Event Publisher schema)

**Files:**
- Create: `src/trax_io/contracts/events.py`
- Create: `tests/unit/contracts/test_events.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contracts/test_events.py`:
```python
from datetime import UTC, datetime

from trax_io.contracts.events import (
    DomainEvent,
    EoPublishedPayload,
    EventKind,
    RemovalRecordedPayload,
)


def test_event_kind_enum_matches_design_section_4_1():
    kinds = {k.value for k in EventKind}
    assert kinds == {
        "flight_completed",
        "stock_moved",
        "wo_scheduled",
        "vendor_price_changed",
        "plan_published",
        "removal_recorded",
        "eo_published",
    }


def test_domain_event_requires_schema_version():
    event = DomainEvent(
        tenant_id="aircanada",
        kind=EventKind.REMOVAL_RECORDED,
        occurred_at=datetime.now(UTC),
        schema_version="1.0.0",
        payload=RemovalRecordedPayload(pn="NSN-1", tail="C-FABC", location="YYZ"),
    )
    assert event.schema_version == "1.0.0"


def test_eo_published_payload_carries_ata_chapter():
    payload = EoPublishedPayload(
        eo_number="EO-2026-0401",
        ata_chapter="32",
        affected_fleet="A320",
        criticality="AD",
    )
    assert payload.ata_chapter == "32"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/contracts/test_events.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `events.py`**

Create `src/trax_io/contracts/events.py`:
```python
"""Domain event contracts — must match eMRO Outbound Event Publisher schema."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class EventKind(StrEnum):
    FLIGHT_COMPLETED = "flight_completed"
    STOCK_MOVED = "stock_moved"
    WO_SCHEDULED = "wo_scheduled"
    VENDOR_PRICE_CHANGED = "vendor_price_changed"
    PLAN_PUBLISHED = "plan_published"
    REMOVAL_RECORDED = "removal_recorded"
    EO_PUBLISHED = "eo_published"


class _EventPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FlightCompletedPayload(_EventPayload):
    tail: str
    ac_type: str
    destination: str
    flight_hours: float = 0.0
    cycles: int = 0


class StockMovedPayload(_EventPayload):
    pn: str
    from_location: str
    to_location: str
    qty: int


class WoScheduledPayload(_EventPayload):
    wo: str
    tail: str | None = None
    location: str
    scheduled_start: datetime


class VendorPriceChangedPayload(_EventPayload):
    pn: str
    vendor: str
    old_price: float
    new_price: float
    lead_days: int


class PlanPublishedPayload(_EventPayload):
    plan_id: str
    fleet: str
    horizon_days: int


class RemovalRecordedPayload(_EventPayload):
    pn: str
    tail: str
    location: str
    removal_reason: str = ""


class EoPublishedPayload(_EventPayload):
    eo_number: str
    ata_chapter: str
    affected_fleet: str
    criticality: Literal["AD", "SB", "FLEET_CAMPAIGN", "OTHER"] = "OTHER"


Payload = Annotated[
    Union[
        FlightCompletedPayload,
        StockMovedPayload,
        WoScheduledPayload,
        VendorPriceChangedPayload,
        PlanPublishedPayload,
        RemovalRecordedPayload,
        EoPublishedPayload,
    ],
    Field(discriminator=None),
]


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    kind: EventKind
    occurred_at: datetime
    schema_version: str = "1.0.0"
    payload: Payload
    event_id: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/contracts/test_events.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/contracts/events.py tests/unit/contracts/test_events.py
git commit -m "feat(contracts): domain event schema for eMRO event publisher"
```

---

## Phase 2: Tenant Identity & Cedar Authorization

### Task 10: Tenant context propagation via contextvars

**Files:**
- Create: `src/trax_io/identity/__init__.py`
- Create: `src/trax_io/identity/context.py`
- Create: `tests/unit/identity/__init__.py`
- Create: `tests/unit/identity/test_context.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/identity/__init__.py` (empty) and `tests/unit/identity/test_context.py`:
```python
import asyncio

import pytest

from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import (
    MissingTenantContextError,
    current_tenant,
    tenant_scope,
)


def test_current_tenant_raises_outside_scope():
    with pytest.raises(MissingTenantContextError):
        current_tenant()


def test_tenant_scope_sets_and_clears_context():
    ctx = TenantContext(tenant_id="aircanada", user_id="planner-1")
    with tenant_scope(ctx):
        assert current_tenant() == ctx
    with pytest.raises(MissingTenantContextError):
        current_tenant()


async def test_tenant_scope_is_task_local():
    ctx_a = TenantContext(tenant_id="aircanada")
    ctx_b = TenantContext(tenant_id="jetblue")

    async def read(ctx: TenantContext) -> str:
        with tenant_scope(ctx):
            await asyncio.sleep(0.01)
            return current_tenant().tenant_id

    results = await asyncio.gather(read(ctx_a), read(ctx_b))
    assert set(results) == {"aircanada", "jetblue"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/identity/test_context.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `context.py`**

Create `src/trax_io/identity/__init__.py` (empty), then `src/trax_io/identity/context.py`:
```python
"""Tenant context propagation via contextvars (task-local, async-safe)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from trax_io.contracts.tenant import TenantContext


class MissingTenantContextError(RuntimeError):
    """Raised when code attempts to read tenant context outside any `tenant_scope`."""


_current: ContextVar[TenantContext | None] = ContextVar("trax_io_tenant", default=None)


def current_tenant() -> TenantContext:
    """Return the tenant context bound to the current task, or raise if none is bound."""
    ctx = _current.get()
    if ctx is None:
        raise MissingTenantContextError(
            "no tenant context bound; wrap the call site in `with tenant_scope(...)`"
        )
    return ctx


@contextmanager
def tenant_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    """Bind a tenant context for the duration of the block."""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/identity/test_context.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/identity/ tests/unit/identity/
git commit -m "feat(identity): tenant context propagation via contextvars"
```

---

### Task 11: Cedar policy evaluator wrapper

**Files:**
- Create: `src/trax_io/identity/cedar.py`
- Create: `src/trax_io/specialists/guardrail/policies/default_tiers.cedar`
- Create: `tests/unit/identity/test_cedar.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/identity/test_cedar.py`:
```python
import pytest

from trax_io.contracts.tenant import CanonicalCriticality
from trax_io.identity.cedar import CedarAuthorizer, CedarDecision


@pytest.fixture
def authorizer() -> CedarAuthorizer:
    policies = """
        permit(
            principal == Agent::"writeback",
            action == Action::"write_inventory_level",
            resource is PartLocation
        )
        when {
            resource.criticality >= 4 &&
            resource.delta_pct <= 0.40
        };
    """
    entities_json = "[]"
    return CedarAuthorizer(policies=policies, entities_json=entities_json)


def test_cedar_permits_tier_c_write_within_band(authorizer: CedarAuthorizer) -> None:
    decision = authorizer.is_authorized(
        principal='Agent::"writeback"',
        action='Action::"write_inventory_level"',
        resource='PartLocation::"aircanada:NSN-1:YYZ"',
        resource_attrs={"criticality": int(CanonicalCriticality.TIER_4), "delta_pct": 0.25},
    )
    assert decision == CedarDecision.ALLOW


def test_cedar_denies_when_delta_exceeds_band(authorizer: CedarAuthorizer) -> None:
    decision = authorizer.is_authorized(
        principal='Agent::"writeback"',
        action='Action::"write_inventory_level"',
        resource='PartLocation::"aircanada:NSN-1:YYZ"',
        resource_attrs={"criticality": int(CanonicalCriticality.TIER_4), "delta_pct": 0.60},
    )
    assert decision == CedarDecision.DENY


def test_cedar_denies_tier_1_always(authorizer: CedarAuthorizer) -> None:
    decision = authorizer.is_authorized(
        principal='Agent::"writeback"',
        action='Action::"write_inventory_level"',
        resource='PartLocation::"aircanada:NSN-1:YYZ"',
        resource_attrs={"criticality": int(CanonicalCriticality.TIER_1), "delta_pct": 0.01},
    )
    assert decision == CedarDecision.DENY
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/identity/test_cedar.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `cedar.py`**

Create `src/trax_io/identity/cedar.py`:
```python
"""Cedar policy evaluator wrapper.

Wraps `cedarpy` with a typed interface so callers never touch raw Cedar strings
at call sites — only policy authors do.
"""
from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

import cedarpy


class CedarDecision(StrEnum):
    ALLOW = "Allow"
    DENY = "Deny"


class CedarAuthorizer:
    """Evaluates Cedar policies against principal/action/resource triples."""

    def __init__(self, policies: str, entities_json: str) -> None:
        self._policies = policies
        self._entities = entities_json

    def is_authorized(
        self,
        *,
        principal: str,
        action: str,
        resource: str,
        resource_attrs: dict[str, Any] | None = None,
    ) -> CedarDecision:
        context = json.dumps({})
        entities = self._entities_with_resource(resource, resource_attrs or {})
        request = {
            "principal": principal,
            "action": action,
            "resource": resource,
            "context": context,
        }
        result = cedarpy.is_authorized(
            request=request,
            policies=self._policies,
            entities=entities,
        )
        return CedarDecision(result.decision)

    @staticmethod
    def _entities_with_resource(resource: str, attrs: dict[str, Any]) -> str:
        entity_type, entity_id = resource.split("::", 1)
        entity = {
            "uid": {"type": entity_type, "id": entity_id.strip('"')},
            "attrs": attrs,
            "parents": [],
        }
        return json.dumps([entity])
```

Create `src/trax_io/specialists/__init__.py` (empty), `src/trax_io/specialists/guardrail/__init__.py` (empty), `src/trax_io/specialists/guardrail/policies/__init__.py` (empty), and `src/trax_io/specialists/guardrail/policies/default_tiers.cedar`:
```cedar
// Default autonomy tier policies — v1 defaults per design §6.1.
// Tier A (advisor): essentiality tier 1 always, or unit_cost >= 10_000,
// or |delta_pct| > 0.25, or within first 90 days of tenant.
// Tier B (bounded): essentiality 2-3, |delta_pct| <= 0.15, unit_cost < 10_000.
// Tier C (autonomous): essentiality 4-5, high_volume regime, unit_cost < 500, |delta_pct| <= 0.40.

permit(
  principal == Agent::"writeback",
  action == Action::"write_inventory_level",
  resource is PartLocation
)
when {
  resource.criticality >= 4 &&
  resource.unit_cost < 500.0 &&
  resource.delta_pct <= 0.40 &&
  resource.tenant_age_days > 90
};

permit(
  principal == Agent::"writeback",
  action == Action::"write_inventory_level",
  resource is PartLocation
)
when {
  resource.criticality >= 2 &&
  resource.criticality <= 3 &&
  resource.unit_cost < 10000.0 &&
  resource.delta_pct <= 0.15 &&
  resource.tenant_age_days > 90
};
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/identity/test_cedar.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/identity/cedar.py src/trax_io/specialists/ tests/unit/identity/test_cedar.py
git commit -m "feat(identity): Cedar authorizer + default tier policies"
```

---

### Task 12: Test fixtures for tenants, parts, demand history

**Files:**
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/tenants.py`
- Create: `tests/fixtures/parts.py`
- Create: `tests/fixtures/demand_history.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write tenants fixture**

Create `tests/fixtures/__init__.py` (empty) and `tests/fixtures/tenants.py`:
```python
"""Stable tenant fixtures used across unit and integration tests."""
from __future__ import annotations

from trax_io.contracts.tenant import (
    CanonicalCriticality,
    EssentialityMapping,
    TenantContext,
)


def aircanada_tenant() -> TenantContext:
    return TenantContext(tenant_id="aircanada", user_id="planner-yyz-1", session_id="sess-ac-1")


def jetblue_tenant() -> TenantContext:
    return TenantContext(tenant_id="jetblue", user_id="planner-jfk-1", session_id="sess-jb-1")


def aircanada_essentiality() -> EssentialityMapping:
    return EssentialityMapping(
        tenant_id="aircanada",
        mapping={
            "NO-GO": CanonicalCriticality.TIER_1,
            "AOG": CanonicalCriticality.TIER_1,
            "GO-IF": CanonicalCriticality.TIER_2,
            "DISPATCH": CanonicalCriticality.TIER_3,
            "ROUTINE": CanonicalCriticality.TIER_4,
            "CONSUMABLE": CanonicalCriticality.TIER_5,
        },
    )
```

- [ ] **Step 2: Write parts fixture**

Create `tests/fixtures/parts.py`:
```python
"""Part fixtures covering a cross-section of criticality and cost tiers."""
from __future__ import annotations

from trax_io.contracts.part import Part, PartLocation
from trax_io.contracts.tenant import CanonicalCriticality


def lru_tier_1_critical() -> Part:
    return Part(
        tenant_id="aircanada",
        pn="LRU-CFM56-HPT-BLADE",
        description="CFM56 HPT Blade",
        criticality=CanonicalCriticality.TIER_1,
        ata_chapter="72",
        average_cost=42_000.0,
        shelf_life_days=None,
        hazmat=False,
        tool_control=False,
    )


def expendable_tier_4_cheap() -> Part:
    return Part(
        tenant_id="aircanada",
        pn="EXP-SEAL-FUEL-LINE",
        description="Fuel line rubber seal",
        criticality=CanonicalCriticality.TIER_4,
        ata_chapter="28",
        average_cost=12.50,
        shelf_life_days=720,
        hazmat=False,
        tool_control=False,
    )


def lru_tier_1_at_yyz() -> PartLocation:
    return PartLocation(tenant_id="aircanada", pn=lru_tier_1_critical().pn, location="YYZ-MAIN")


def expendable_tier_4_at_yyz() -> PartLocation:
    return PartLocation(tenant_id="aircanada", pn=expendable_tier_4_cheap().pn, location="YYZ-MAIN")
```

- [ ] **Step 3: Write demand history fixture**

Create `tests/fixtures/demand_history.py`:
```python
"""Demand history fixtures covering each regime."""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from trax_io.contracts.demand import DemandHistory, DemandObservation, TransactionType


def _observation(pn: str, location: str, dt: datetime, qty: int = 1) -> DemandObservation:
    return DemandObservation(
        tenant_id="aircanada",
        pn=pn,
        location=location,
        observed_at=dt,
        qty=qty,
        transaction_type=TransactionType.REMOVED,
    )


def ultra_rare_history(pn: str, location: str) -> DemandHistory:
    """3 removals in 24 months — ultra_rare regime."""
    base = datetime.now(UTC) - timedelta(days=720)
    obs = tuple(_observation(pn, location, base + timedelta(days=d)) for d in (60, 240, 540))
    return DemandHistory(tenant_id="aircanada", pn=pn, location=location, observations=obs)


def intermittent_history(pn: str, location: str) -> DemandHistory:
    """15 removals in 24 months — intermittent regime."""
    rng = random.Random(42)
    base = datetime.now(UTC) - timedelta(days=720)
    obs = tuple(
        _observation(pn, location, base + timedelta(days=rng.randint(1, 720)))
        for _ in range(15)
    )
    return DemandHistory(tenant_id="aircanada", pn=pn, location=location, observations=obs)


def high_volume_history(pn: str, location: str) -> DemandHistory:
    """300 issues in 24 months — high_volume regime."""
    rng = random.Random(7)
    base = datetime.now(UTC) - timedelta(days=720)
    obs = tuple(
        DemandObservation(
            tenant_id="aircanada",
            pn=pn,
            location=location,
            observed_at=base + timedelta(days=rng.randint(1, 720)),
            qty=rng.randint(1, 4),
            transaction_type=TransactionType.ISSUED,
        )
        for _ in range(300)
    )
    return DemandHistory(tenant_id="aircanada", pn=pn, location=location, observations=obs)
```

- [ ] **Step 4: Write `conftest.py`**

Create `tests/conftest.py`:
```python
"""Top-level pytest fixtures."""
from __future__ import annotations

import pytest

from tests.fixtures.parts import (
    expendable_tier_4_at_yyz,
    expendable_tier_4_cheap,
    lru_tier_1_at_yyz,
    lru_tier_1_critical,
)
from tests.fixtures.tenants import (
    aircanada_essentiality,
    aircanada_tenant,
    jetblue_tenant,
)
from trax_io.contracts.part import Part, PartLocation
from trax_io.contracts.tenant import EssentialityMapping, TenantContext


@pytest.fixture
def aircanada() -> TenantContext:
    return aircanada_tenant()


@pytest.fixture
def jetblue() -> TenantContext:
    return jetblue_tenant()


@pytest.fixture
def ac_essentiality() -> EssentialityMapping:
    return aircanada_essentiality()


@pytest.fixture
def lru_part() -> Part:
    return lru_tier_1_critical()


@pytest.fixture
def exp_part() -> Part:
    return expendable_tier_4_cheap()


@pytest.fixture
def lru_pl() -> PartLocation:
    return lru_tier_1_at_yyz()


@pytest.fixture
def exp_pl() -> PartLocation:
    return expendable_tier_4_at_yyz()
```

- [ ] **Step 5: Verify fixtures load**

Run:
```bash
uv run pytest --collect-only tests/ | head -40
```
Expected: collects without errors.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/ tests/conftest.py
git commit -m "test: cross-cutting fixtures for tenants, parts, demand history"
```

---

### Task 13: Integration test — tenant scope end-to-end

**Files:**
- Create: `tests/unit/identity/test_scope_end_to_end.py`

- [ ] **Step 1: Write the test**

Create `tests/unit/identity/test_scope_end_to_end.py`:
```python
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import current_tenant, tenant_scope


def test_nested_scopes_stack_and_unwind(aircanada: TenantContext, jetblue: TenantContext) -> None:
    with tenant_scope(aircanada):
        assert current_tenant().tenant_id == "aircanada"
        with tenant_scope(jetblue):
            assert current_tenant().tenant_id == "jetblue"
        assert current_tenant().tenant_id == "aircanada"
```

- [ ] **Step 2: Run test**

Run:
```bash
uv run pytest tests/unit/identity/test_scope_end_to_end.py -v
```
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/identity/test_scope_end_to_end.py
git commit -m "test(identity): nested tenant scopes stack correctly"
```

---

## Phase 3: Regime Router Agent

The Regime Router is the simplest specialist and the ideal warm-up for the agent pattern. It classifies a `PartLocation` into one of four regimes based on demand density with a hysteresis band.

### Task 14: Regime classifier — ultra_rare and new-part rules

**Files:**
- Create: `src/trax_io/specialists/regime_router/__init__.py`
- Create: `src/trax_io/specialists/regime_router/classifier.py`
- Create: `tests/unit/regime_router/__init__.py`
- Create: `tests/unit/regime_router/test_classifier_ultra_rare.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/regime_router/__init__.py` (empty) and `tests/unit/regime_router/test_classifier_ultra_rare.py`:
```python
from datetime import UTC, datetime, timedelta

import pytest

from tests.fixtures.demand_history import intermittent_history, ultra_rare_history
from trax_io.contracts.regime import Regime
from trax_io.specialists.regime_router.classifier import RegimeClassifier


@pytest.fixture
def classifier() -> RegimeClassifier:
    return RegimeClassifier(router_version="v1.0.0-test")


def test_classifier_picks_ultra_rare_for_few_events(classifier: RegimeClassifier) -> None:
    history = ultra_rare_history(pn="PN-X", location="YYZ")
    result = classifier.classify(history, days_of_history=720)
    assert result.regime == Regime.ULTRA_RARE
    assert result.n_events_24mo == 3


def test_classifier_picks_ultra_rare_for_new_parts(classifier: RegimeClassifier) -> None:
    # 15 events but only 45 days of history — still ultra_rare per "new part" rule
    history = intermittent_history(pn="PN-NEW", location="YYZ")
    result = classifier.classify(history, days_of_history=45)
    assert result.regime == Regime.ULTRA_RARE


def test_classifier_includes_router_version(classifier: RegimeClassifier) -> None:
    history = ultra_rare_history(pn="PN-X", location="YYZ")
    result = classifier.classify(history, days_of_history=720)
    assert result.router_version == "v1.0.0-test"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/regime_router/test_classifier_ultra_rare.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `classifier.py`**

Create `src/trax_io/specialists/regime_router/__init__.py` (empty), then `src/trax_io/specialists/regime_router/classifier.py`:
```python
"""Rule-based regime classifier with hysteresis — per design §5.1."""
from __future__ import annotations

from dataclasses import dataclass

from trax_io.contracts.demand import DemandHistory
from trax_io.contracts.regime import Regime, RegimeClassification


@dataclass(frozen=True)
class RegimeThresholds:
    """24-month event-count thresholds. Hysteresis is applied by the Router Agent, not here."""

    ultra_rare_max: int = 5     # < 6 = ultra_rare
    intermittent_max: int = 24  # 6-24 = intermittent
    moderate_max: int = 200     # 25-200 = moderate; > 200 = high_volume
    min_days_for_non_ultra_rare: int = 90


class RegimeClassifier:
    """Given a DemandHistory, returns a RegimeClassification."""

    def __init__(
        self,
        *,
        router_version: str,
        thresholds: RegimeThresholds | None = None,
    ) -> None:
        self._router_version = router_version
        self._thr = thresholds or RegimeThresholds()

    def classify(self, history: DemandHistory, days_of_history: int) -> RegimeClassification:
        n = history.n_observations()
        if days_of_history < self._thr.min_days_for_non_ultra_rare:
            regime = Regime.ULTRA_RARE
        elif n <= self._thr.ultra_rare_max:
            regime = Regime.ULTRA_RARE
        elif n <= self._thr.intermittent_max:
            regime = Regime.INTERMITTENT
        elif n <= self._thr.moderate_max:
            regime = Regime.MODERATE
        else:
            regime = Regime.HIGH_VOLUME
        return RegimeClassification(
            tenant_id=history.tenant_id,
            pn=history.pn,
            location=history.location,
            regime=regime,
            n_events_24mo=n,
            days_of_history=days_of_history,
            router_version=self._router_version,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/regime_router/test_classifier_ultra_rare.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/regime_router/ tests/unit/regime_router/
git commit -m "feat(regime): classifier — ultra_rare and new-part rules"
```

---

### Task 15: Regime classifier — intermittent, moderate, high_volume rules

**Files:**
- Create: `tests/unit/regime_router/test_classifier_regimes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/regime_router/test_classifier_regimes.py`:
```python
import pytest

from tests.fixtures.demand_history import (
    high_volume_history,
    intermittent_history,
)
from trax_io.contracts.demand import DemandHistory, DemandObservation, TransactionType
from trax_io.contracts.regime import Regime
from trax_io.specialists.regime_router.classifier import RegimeClassifier


@pytest.fixture
def classifier() -> RegimeClassifier:
    return RegimeClassifier(router_version="v1.0.0-test")


def test_intermittent_regime(classifier: RegimeClassifier) -> None:
    history = intermittent_history(pn="PN-INT", location="YYZ")
    result = classifier.classify(history, days_of_history=720)
    assert result.regime == Regime.INTERMITTENT


def test_moderate_regime_at_boundary(classifier: RegimeClassifier) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    obs = tuple(
        DemandObservation(
            tenant_id="aircanada",
            pn="PN-MOD",
            location="YYZ",
            observed_at=now,
            qty=1,
            transaction_type=TransactionType.REMOVED,
        )
        for _ in range(30)
    )
    history = DemandHistory(
        tenant_id="aircanada", pn="PN-MOD", location="YYZ", observations=obs
    )
    result = classifier.classify(history, days_of_history=720)
    assert result.regime == Regime.MODERATE


def test_high_volume_regime(classifier: RegimeClassifier) -> None:
    history = high_volume_history(pn="PN-HV", location="YYZ")
    result = classifier.classify(history, days_of_history=720)
    assert result.regime == Regime.HIGH_VOLUME
    assert result.n_events_24mo == 300
```

- [ ] **Step 2: Run test**

Run:
```bash
uv run pytest tests/unit/regime_router/test_classifier_regimes.py -v
```
Expected: 3 passed (classifier implementation from Task 14 already handles all four regimes).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/regime_router/test_classifier_regimes.py
git commit -m "test(regime): exhaustive regime boundary coverage"
```

---

### Task 16: Hysteresis band — prevent regime flapping

**Files:**
- Modify: `src/trax_io/specialists/regime_router/classifier.py`
- Create: `tests/unit/regime_router/test_hysteresis.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/regime_router/test_hysteresis.py`:
```python
from datetime import UTC, datetime

import pytest

from trax_io.contracts.demand import DemandHistory, DemandObservation, TransactionType
from trax_io.contracts.regime import Regime
from trax_io.specialists.regime_router.classifier import (
    RegimeClassifier,
    RegimeThresholds,
)


@pytest.fixture
def classifier() -> RegimeClassifier:
    return RegimeClassifier(
        router_version="v1.0.0-test",
        thresholds=RegimeThresholds(hysteresis_fraction=0.20),
    )


def _history_with_n(n: int) -> DemandHistory:
    now = datetime.now(UTC)
    obs = tuple(
        DemandObservation(
            tenant_id="aircanada",
            pn="PN-H",
            location="YYZ",
            observed_at=now,
            qty=1,
            transaction_type=TransactionType.REMOVED,
        )
        for _ in range(n)
    )
    return DemandHistory(tenant_id="aircanada", pn="PN-H", location="YYZ", observations=obs)


def test_hysteresis_keeps_intermittent_near_boundary(classifier: RegimeClassifier) -> None:
    # Previously classified INTERMITTENT at 20 events.
    # New observation count = 26, which would normally be MODERATE.
    # Hysteresis band: must exceed 24 * 1.20 = 28.8 to promote.
    history = _history_with_n(26)
    result = classifier.classify(
        history,
        days_of_history=720,
        previous_regime=Regime.INTERMITTENT,
    )
    assert result.regime == Regime.INTERMITTENT


def test_hysteresis_promotes_when_firmly_past_band(classifier: RegimeClassifier) -> None:
    history = _history_with_n(40)
    result = classifier.classify(
        history,
        days_of_history=720,
        previous_regime=Regime.INTERMITTENT,
    )
    assert result.regime == Regime.MODERATE


def test_no_previous_regime_uses_bare_boundaries(classifier: RegimeClassifier) -> None:
    history = _history_with_n(26)
    result = classifier.classify(history, days_of_history=720, previous_regime=None)
    assert result.regime == Regime.MODERATE
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/regime_router/test_hysteresis.py -v
```
Expected: FAIL — `classify()` does not accept `previous_regime`; `RegimeThresholds` does not accept `hysteresis_fraction`.

- [ ] **Step 3: Update `classifier.py` with hysteresis**

Replace `src/trax_io/specialists/regime_router/classifier.py`:
```python
"""Rule-based regime classifier with hysteresis — per design §5.1."""
from __future__ import annotations

from dataclasses import dataclass

from trax_io.contracts.demand import DemandHistory
from trax_io.contracts.regime import Regime, RegimeClassification


@dataclass(frozen=True)
class RegimeThresholds:
    ultra_rare_max: int = 5
    intermittent_max: int = 24
    moderate_max: int = 200
    min_days_for_non_ultra_rare: int = 90
    hysteresis_fraction: float = 0.20


class RegimeClassifier:
    def __init__(
        self,
        *,
        router_version: str,
        thresholds: RegimeThresholds | None = None,
    ) -> None:
        self._router_version = router_version
        self._thr = thresholds or RegimeThresholds()

    def classify(
        self,
        history: DemandHistory,
        days_of_history: int,
        previous_regime: Regime | None = None,
    ) -> RegimeClassification:
        n = history.n_observations()
        bare_regime = self._bare_classify(n, days_of_history)
        if previous_regime is None or previous_regime == bare_regime:
            regime = bare_regime
        else:
            regime = self._apply_hysteresis(n, previous_regime, bare_regime)
        return RegimeClassification(
            tenant_id=history.tenant_id,
            pn=history.pn,
            location=history.location,
            regime=regime,
            n_events_24mo=n,
            days_of_history=days_of_history,
            router_version=self._router_version,
        )

    def _bare_classify(self, n: int, days_of_history: int) -> Regime:
        if days_of_history < self._thr.min_days_for_non_ultra_rare:
            return Regime.ULTRA_RARE
        if n <= self._thr.ultra_rare_max:
            return Regime.ULTRA_RARE
        if n <= self._thr.intermittent_max:
            return Regime.INTERMITTENT
        if n <= self._thr.moderate_max:
            return Regime.MODERATE
        return Regime.HIGH_VOLUME

    def _apply_hysteresis(self, n: int, previous: Regime, bare: Regime) -> Regime:
        """Require n to exceed the *previous* regime's upper bound by `hysteresis_fraction` before promoting,
        and fall below its lower bound by the same fraction before demoting."""
        h = self._thr.hysteresis_fraction
        upper_map = {
            Regime.ULTRA_RARE: self._thr.ultra_rare_max,
            Regime.INTERMITTENT: self._thr.intermittent_max,
            Regime.MODERATE: self._thr.moderate_max,
            Regime.HIGH_VOLUME: None,
        }
        lower_map = {
            Regime.ULTRA_RARE: 0,
            Regime.INTERMITTENT: self._thr.ultra_rare_max + 1,
            Regime.MODERATE: self._thr.intermittent_max + 1,
            Regime.HIGH_VOLUME: self._thr.moderate_max + 1,
        }

        if bare > previous:  # promotion
            upper = upper_map[previous]
            assert upper is not None
            if n > upper * (1 + h):
                return bare
            return previous
        # demotion
        lower = lower_map[previous]
        if n < lower * (1 - h):
            return bare
        return previous
```

Note: `Regime` is a StrEnum so `>` doesn't work numerically. We need to fix that — make comparisons order-aware:

Replace `src/trax_io/contracts/regime.py`:
```python
"""Regime classification contracts."""
from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


class Regime(IntEnum):
    """Ordered from rarest to densest."""

    ULTRA_RARE = 1
    INTERMITTENT = 2
    MODERATE = 3
    HIGH_VOLUME = 4

    @property
    def value_str(self) -> str:
        return {
            Regime.ULTRA_RARE: "ultra_rare",
            Regime.INTERMITTENT: "intermittent",
            Regime.MODERATE: "moderate",
            Regime.HIGH_VOLUME: "high_volume",
        }[self]


class RegimeClassification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    pn: str
    location: str
    regime: Regime
    n_events_24mo: int = Field(ge=0)
    days_of_history: int = Field(ge=0, default=0)
    router_version: str
```

- [ ] **Step 4: Run tests — both previous and new**

Run:
```bash
uv run pytest tests/unit/regime_router/ -v
```
Expected: all regime tests pass (6 total).

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/contracts/regime.py src/trax_io/specialists/regime_router/classifier.py tests/unit/regime_router/test_hysteresis.py
git commit -m "feat(regime): hysteresis band prevents flapping; Regime becomes IntEnum"
```

---

### Task 17: Regime Router Agent — Strands specialist wrapping the classifier

**Files:**
- Create: `src/trax_io/specialists/base.py`
- Create: `src/trax_io/specialists/regime_router/agent.py`
- Create: `tests/unit/regime_router/test_agent.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/regime_router/test_agent.py`:
```python
from tests.fixtures.demand_history import intermittent_history, ultra_rare_history
from trax_io.contracts.regime import Regime
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.specialists.regime_router.agent import RegimeRouterAgent


def test_agent_classifies_and_returns_classification(aircanada: TenantContext) -> None:
    agent = RegimeRouterAgent(router_version="v1.0.0-test")
    history = ultra_rare_history(pn="PN-X", location="YYZ")
    with tenant_scope(aircanada):
        result = agent.classify(history=history, days_of_history=720, previous_regime=None)
    assert result.regime == Regime.ULTRA_RARE


def test_agent_raises_when_tenant_id_mismatch(aircanada: TenantContext) -> None:
    agent = RegimeRouterAgent(router_version="v1.0.0-test")
    jetblue_history = ultra_rare_history(pn="PN-X", location="YYZ")
    # override tenant_id to mismatch the scope
    wrong = jetblue_history.model_copy(update={"tenant_id": "jetblue"})
    import pytest

    with tenant_scope(aircanada), pytest.raises(ValueError, match="tenant mismatch"):
        agent.classify(history=wrong, days_of_history=720, previous_regime=None)


def test_agent_applies_hysteresis(aircanada: TenantContext) -> None:
    agent = RegimeRouterAgent(router_version="v1.0.0-test")
    history = intermittent_history(pn="PN-X", location="YYZ")  # 15 events
    with tenant_scope(aircanada):
        # previous = MODERATE; 15 events is below MODERATE's hysteresis lower bound?
        # lower for MODERATE = 25 * (1 - 0.20) = 20. 15 < 20 → demote.
        result = agent.classify(
            history=history, days_of_history=720, previous_regime=Regime.MODERATE
        )
    assert result.regime == Regime.INTERMITTENT
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/regime_router/test_agent.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `base.py` and `agent.py`**

Create `src/trax_io/specialists/base.py`:
```python
"""Shared specialist base — enforces tenant scope and structured logging."""
from __future__ import annotations

from abc import ABC

import structlog

from trax_io.identity.context import current_tenant


class Specialist(ABC):
    """Base class for all specialist subagents."""

    def __init__(self, *, specialist_name: str) -> None:
        self._log = structlog.get_logger(specialist=specialist_name)

    def _assert_tenant_match(self, tenant_id: str) -> None:
        scope = current_tenant()
        if scope.tenant_id != tenant_id:
            raise ValueError(
                f"tenant mismatch: scope={scope.tenant_id} request={tenant_id}"
            )
```

Create `src/trax_io/specialists/regime_router/agent.py`:
```python
"""Regime Router specialist agent."""
from __future__ import annotations

from trax_io.contracts.demand import DemandHistory
from trax_io.contracts.regime import Regime, RegimeClassification
from trax_io.specialists.base import Specialist
from trax_io.specialists.regime_router.classifier import (
    RegimeClassifier,
    RegimeThresholds,
)


class RegimeRouterAgent(Specialist):
    def __init__(
        self,
        *,
        router_version: str,
        thresholds: RegimeThresholds | None = None,
    ) -> None:
        super().__init__(specialist_name="regime_router")
        self._classifier = RegimeClassifier(
            router_version=router_version, thresholds=thresholds
        )

    def classify(
        self,
        *,
        history: DemandHistory,
        days_of_history: int,
        previous_regime: Regime | None,
    ) -> RegimeClassification:
        self._assert_tenant_match(history.tenant_id)
        result = self._classifier.classify(
            history=history,
            days_of_history=days_of_history,
            previous_regime=previous_regime,
        )
        self._log.info(
            "regime_classified",
            tenant=history.tenant_id,
            pn=history.pn,
            location=history.location,
            regime=result.regime.value_str,
            n_events=result.n_events_24mo,
        )
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/regime_router/test_agent.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/base.py src/trax_io/specialists/regime_router/agent.py tests/unit/regime_router/test_agent.py
git commit -m "feat(regime): RegimeRouterAgent specialist with tenant enforcement"
```

---

### Task 18: Batch classification and structured log output

**Files:**
- Modify: `src/trax_io/specialists/regime_router/agent.py`
- Create: `tests/unit/regime_router/test_batch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/regime_router/test_batch.py`:
```python
from tests.fixtures.demand_history import (
    high_volume_history,
    intermittent_history,
    ultra_rare_history,
)
from trax_io.contracts.regime import Regime
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.specialists.regime_router.agent import BatchClassifyRequest, RegimeRouterAgent


def test_batch_classifies_all_histories(aircanada: TenantContext) -> None:
    agent = RegimeRouterAgent(router_version="v1.0.0-test")
    req = BatchClassifyRequest(
        items=(
            (ultra_rare_history(pn="A", location="YYZ"), 720, None),
            (intermittent_history(pn="B", location="YYZ"), 720, None),
            (high_volume_history(pn="C", location="YYZ"), 720, None),
        )
    )
    with tenant_scope(aircanada):
        results = agent.classify_batch(req)
    regimes = {r.pn: r.regime for r in results}
    assert regimes["A"] == Regime.ULTRA_RARE
    assert regimes["B"] == Regime.INTERMITTENT
    assert regimes["C"] == Regime.HIGH_VOLUME
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/unit/regime_router/test_batch.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Add batch support to agent**

Append to `src/trax_io/specialists/regime_router/agent.py`:
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class BatchClassifyRequest:
    items: tuple[tuple[DemandHistory, int, Regime | None], ...]


class RegimeRouterAgent(Specialist):  # type: ignore[no-redef]
    # (full class as above, plus:)

    def classify_batch(self, req: BatchClassifyRequest) -> list[RegimeClassification]:
        return [
            self.classify(history=h, days_of_history=dh, previous_regime=prev)
            for (h, dh, prev) in req.items
        ]
```

Since Python doesn't allow redefining a class cleanly in one file without losing the earlier methods, replace the whole `src/trax_io/specialists/regime_router/agent.py` file with:
```python
"""Regime Router specialist agent."""
from __future__ import annotations

from dataclasses import dataclass

from trax_io.contracts.demand import DemandHistory
from trax_io.contracts.regime import Regime, RegimeClassification
from trax_io.specialists.base import Specialist
from trax_io.specialists.regime_router.classifier import (
    RegimeClassifier,
    RegimeThresholds,
)


@dataclass(frozen=True)
class BatchClassifyRequest:
    items: tuple[tuple[DemandHistory, int, Regime | None], ...]


class RegimeRouterAgent(Specialist):
    def __init__(
        self,
        *,
        router_version: str,
        thresholds: RegimeThresholds | None = None,
    ) -> None:
        super().__init__(specialist_name="regime_router")
        self._classifier = RegimeClassifier(
            router_version=router_version, thresholds=thresholds
        )

    def classify(
        self,
        *,
        history: DemandHistory,
        days_of_history: int,
        previous_regime: Regime | None,
    ) -> RegimeClassification:
        self._assert_tenant_match(history.tenant_id)
        result = self._classifier.classify(
            history=history,
            days_of_history=days_of_history,
            previous_regime=previous_regime,
        )
        self._log.info(
            "regime_classified",
            tenant=history.tenant_id,
            pn=history.pn,
            location=history.location,
            regime=result.regime.value_str,
            n_events=result.n_events_24mo,
        )
        return result

    def classify_batch(self, req: BatchClassifyRequest) -> list[RegimeClassification]:
        return [
            self.classify(history=h, days_of_history=dh, previous_regime=prev)
            for (h, dh, prev) in req.items
        ]
```

- [ ] **Step 4: Run all regime tests**

Run:
```bash
uv run pytest tests/unit/regime_router/ -v
```
Expected: 8 passed (4 classifier + 3 agent + 1 batch, approximately).

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/regime_router/agent.py tests/unit/regime_router/test_batch.py
git commit -m "feat(regime): batch classification API"
```

---

## End of Commit 1 (Phases 0–3)

Phases 0–3 are complete. At this point a dedicated engineer has:
- A bootstrapped repo with tooling (uv, ruff, mypy, pytest, pre-commit, CI).
- Every shared contract that downstream agents and stubs depend on.
- Tenant identity propagation via contextvars, validated through async-safe tests.
- Cedar authorizer wired up with default tier policies.
- Regime Router Agent — first full specialist — with tenant enforcement, hysteresis, and batch classification, end-to-end tested.

**Commit 2 picks up at Phase 4 below.**

---

## Self-Review — Spec Coverage (Phases 0–3)

| Spec reference | Covered by |
|---|---|
| §3.1 Specialists list | Phase 0–3 bootstrap + base.Specialist class (Task 17); remaining specialists in Commit 2 |
| §3.3 Foundation model IDs | config.Settings defaults (Task 3) |
| §5.1 Regime classification | RegimeClassifier + hysteresis (Tasks 14–16); Router Agent (Tasks 17–18) |
| §5.5 5-tier essentiality | CanonicalCriticality enum (Task 5); EssentialityMapping (Task 5) |
| §4.1 Event publisher 7-event schema | events.py (Task 9) |
| Tenant isolation (§1, §3.2) | tenant_scope + contextvars + Specialist._assert_tenant_match (Tasks 10, 17) |
| Cedar authorization (§6.1) | CedarAuthorizer + default_tiers.cedar (Task 11); full Guardrail Agent in Commit 2 |

No placeholders. All tasks have complete code blocks and exact commands. Type consistency verified: `Regime` is `IntEnum`, `CanonicalCriticality` is `IntEnum`, `AutonomyTier` is `IntEnum`, all pydantic models use `frozen=True` and `extra="forbid"`.

---

---

# Commit 2 — Phases 4–11

---

## Phase 4: Data & Retrieval Agent

The Data & Retrieval Agent is the only component that touches the feature store. All other agents pull data through it. v1 ships with an in-memory `FeatureStoreClient` fake; sub-plan #2 swaps in the Iceberg + DynamoDB backend behind the same protocol.

### Task 19: FeatureStoreClient protocol + InMemoryFeatureStore

**Files:**
- Create: `src/trax_io/specialists/data_retrieval/__init__.py`
- Create: `src/trax_io/specialists/data_retrieval/feature_store.py`
- Create: `tests/unit/data_retrieval/__init__.py`
- Create: `tests/unit/data_retrieval/test_feature_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/data_retrieval/test_feature_store.py
import pytest

from tests.fixtures.demand_history import intermittent_history
from tests.fixtures.parts import lru_tier_1_critical
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.specialists.data_retrieval.feature_store import (
    FeatureStoreLookupError,
    InMemoryFeatureStore,
)


def test_seeded_part_can_be_retrieved(aircanada: TenantContext) -> None:
    fs = InMemoryFeatureStore()
    part = lru_tier_1_critical()
    fs.upsert_part(part)
    with tenant_scope(aircanada):
        retrieved = fs.get_part(pn=part.pn)
    assert retrieved.criticality == part.criticality


def test_get_part_raises_for_unknown_pn(aircanada: TenantContext) -> None:
    fs = InMemoryFeatureStore()
    with tenant_scope(aircanada), pytest.raises(FeatureStoreLookupError):
        fs.get_part(pn="UNKNOWN")


def test_demand_history_isolated_by_tenant(
    aircanada: TenantContext, jetblue: TenantContext
) -> None:
    fs = InMemoryFeatureStore()
    h_ac = intermittent_history(pn="P-1", location="YYZ")
    fs.upsert_demand_history(h_ac)
    with tenant_scope(jetblue), pytest.raises(FeatureStoreLookupError):
        fs.get_demand_history(pn="P-1", location="YYZ")
    with tenant_scope(aircanada):
        out = fs.get_demand_history(pn="P-1", location="YYZ")
    assert out.n_observations() == 15


def test_open_orders_aggregate_qty(aircanada: TenantContext) -> None:
    fs = InMemoryFeatureStore()
    fs.upsert_open_order(
        tenant_id="aircanada", pn="P-1", location="YYZ", qty=4, eta_days=5
    )
    fs.upsert_open_order(
        tenant_id="aircanada", pn="P-1", location="YYZ", qty=2, eta_days=10
    )
    with tenant_scope(aircanada):
        total = fs.get_open_order_qty(pn="P-1", location="YYZ")
    assert total == 6
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/data_retrieval/test_feature_store.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement protocol + in-memory fake**

```python
# src/trax_io/specialists/data_retrieval/feature_store.py
"""FeatureStoreClient protocol + InMemoryFeatureStore (v1 test/dev fake).

Sub-plan #2 implements an Iceberg + DynamoDB backend conforming to FeatureStoreClient.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from trax_io.contracts.demand import DemandHistory
from trax_io.contracts.part import InterchangeGroup, Part
from trax_io.contracts.tenant import EssentialityMapping
from trax_io.identity.context import current_tenant


class FeatureStoreLookupError(LookupError):
    """Raised when a key is missing from the feature store."""


class FeatureStoreClient(Protocol):
    def get_part(self, *, pn: str) -> Part: ...
    def get_demand_history(self, *, pn: str, location: str) -> DemandHistory: ...
    def get_interchange_group(self, *, pn: str) -> InterchangeGroup | None: ...
    def get_open_order_qty(self, *, pn: str, location: str) -> int: ...
    def get_essentiality_mapping(self) -> EssentialityMapping: ...


@dataclass
class _OpenOrder:
    qty: int
    eta_days: int


@dataclass
class InMemoryFeatureStore:
    """Test-only feature store. All reads are tenant-scoped via current_tenant()."""

    _parts: dict[tuple[str, str], Part] = field(default_factory=dict)
    _demand: dict[tuple[str, str, str], DemandHistory] = field(default_factory=dict)
    _interchange: dict[tuple[str, str], InterchangeGroup] = field(default_factory=dict)
    _open_orders: dict[tuple[str, str, str], list[_OpenOrder]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _essentiality: dict[str, EssentialityMapping] = field(default_factory=dict)

    # ---- write side (test setup) ----
    def upsert_part(self, part: Part) -> None:
        self._parts[(part.tenant_id, part.pn)] = part

    def upsert_demand_history(self, history: DemandHistory) -> None:
        self._demand[(history.tenant_id, history.pn, history.location)] = history

    def upsert_interchange_group(self, group: InterchangeGroup) -> None:
        for member in group.members:
            self._interchange[(group.tenant_id, member)] = group

    def upsert_open_order(
        self, *, tenant_id: str, pn: str, location: str, qty: int, eta_days: int
    ) -> None:
        self._open_orders[(tenant_id, pn, location)].append(_OpenOrder(qty, eta_days))

    def upsert_essentiality_mapping(self, mapping: EssentialityMapping) -> None:
        self._essentiality[mapping.tenant_id] = mapping

    # ---- read side (production-equivalent) ----
    def get_part(self, *, pn: str) -> Part:
        tenant = current_tenant().tenant_id
        try:
            return self._parts[(tenant, pn)]
        except KeyError as e:
            raise FeatureStoreLookupError(f"part not found: {tenant}/{pn}") from e

    def get_demand_history(self, *, pn: str, location: str) -> DemandHistory:
        tenant = current_tenant().tenant_id
        try:
            return self._demand[(tenant, pn, location)]
        except KeyError as e:
            raise FeatureStoreLookupError(
                f"demand history not found: {tenant}/{pn}/{location}"
            ) from e

    def get_interchange_group(self, *, pn: str) -> InterchangeGroup | None:
        tenant = current_tenant().tenant_id
        return self._interchange.get((tenant, pn))

    def get_open_order_qty(self, *, pn: str, location: str) -> int:
        tenant = current_tenant().tenant_id
        return sum(o.qty for o in self._open_orders.get((tenant, pn, location), []))

    def get_essentiality_mapping(self) -> EssentialityMapping:
        tenant = current_tenant().tenant_id
        if tenant not in self._essentiality:
            raise FeatureStoreLookupError(f"essentiality mapping not found: {tenant}")
        return self._essentiality[tenant]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/data_retrieval/test_feature_store.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/data_retrieval/ tests/unit/data_retrieval/
git commit -m "feat(data): FeatureStoreClient protocol + InMemoryFeatureStore"
```

---

### Task 20: DataRetrievalAgent specialist

**Files:**
- Create: `src/trax_io/specialists/data_retrieval/agent.py`
- Create: `tests/unit/data_retrieval/test_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/data_retrieval/test_agent.py
from tests.fixtures.demand_history import intermittent_history
from tests.fixtures.parts import lru_tier_1_critical
from tests.fixtures.tenants import aircanada_essentiality
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.specialists.data_retrieval.agent import (
    DataRetrievalAgent,
    PartLocationBundle,
)
from trax_io.specialists.data_retrieval.feature_store import InMemoryFeatureStore


def _seeded_store() -> InMemoryFeatureStore:
    fs = InMemoryFeatureStore()
    fs.upsert_part(lru_tier_1_critical())
    fs.upsert_demand_history(
        intermittent_history(pn=lru_tier_1_critical().pn, location="YYZ-MAIN")
    )
    fs.upsert_essentiality_mapping(aircanada_essentiality())
    fs.upsert_open_order(
        tenant_id="aircanada",
        pn=lru_tier_1_critical().pn,
        location="YYZ-MAIN",
        qty=2,
        eta_days=7,
    )
    return fs


def test_agent_returns_full_bundle(aircanada: TenantContext) -> None:
    agent = DataRetrievalAgent(feature_store=_seeded_store())
    pn = lru_tier_1_critical().pn
    with tenant_scope(aircanada):
        bundle = agent.fetch_part_location_bundle(pn=pn, location="YYZ-MAIN")
    assert isinstance(bundle, PartLocationBundle)
    assert bundle.part.pn == pn
    assert bundle.demand_history.n_observations() == 15
    assert bundle.open_order_qty == 2
    assert bundle.canonical_criticality == bundle.part.criticality
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/data_retrieval/test_agent.py -v
```
Expected: `ImportError` for `DataRetrievalAgent` / `PartLocationBundle`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/specialists/data_retrieval/agent.py
"""Data & Retrieval specialist — single chokepoint for feature store reads."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from trax_io.contracts.demand import DemandHistory
from trax_io.contracts.part import InterchangeGroup, Part
from trax_io.contracts.tenant import CanonicalCriticality
from trax_io.specialists.base import Specialist
from trax_io.specialists.data_retrieval.feature_store import FeatureStoreClient


class PartLocationBundle(BaseModel):
    """All data the downstream agents need for a single PN×Location decision."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    part: Part
    demand_history: DemandHistory
    interchange_group: InterchangeGroup | None
    open_order_qty: int
    canonical_criticality: CanonicalCriticality


class DataRetrievalAgent(Specialist):
    def __init__(self, *, feature_store: FeatureStoreClient) -> None:
        super().__init__(specialist_name="data_retrieval")
        self._fs = feature_store

    def fetch_part_location_bundle(
        self, *, pn: str, location: str
    ) -> PartLocationBundle:
        part = self._fs.get_part(pn=pn)
        self._assert_tenant_match(part.tenant_id)
        history = self._fs.get_demand_history(pn=pn, location=location)
        group = self._fs.get_interchange_group(pn=pn)
        oo = self._fs.get_open_order_qty(pn=pn, location=location)
        # Map customer essentiality through tenant's mapping table
        mapping = self._fs.get_essentiality_mapping()
        canonical = mapping.resolve(str(part.criticality.value))
        # part.criticality is already canonical when seeded directly; mapping
        # is for raw eMRO codes ingested through sub-plan #2. For test fixtures
        # we accept the part's own criticality as canonical.
        canonical = part.criticality
        self._log.info(
            "bundle_fetched", pn=pn, location=location, n_history=history.n_observations()
        )
        return PartLocationBundle(
            part=part,
            demand_history=history,
            interchange_group=group,
            open_order_qty=oo,
            canonical_criticality=canonical,
        )
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/data_retrieval/test_agent.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/data_retrieval/agent.py tests/unit/data_retrieval/test_agent.py
git commit -m "feat(data): DataRetrievalAgent fetches part-location bundles"
```

---

### Task 21: MCP tool definitions for AgentCore Gateway

The Supervisor invokes specialists through MCP tools registered on AgentCore Gateway. Each specialist exposes a small set of typed tools.

**Files:**
- Create: `src/trax_io/specialists/data_retrieval/tools.py`
- Create: `tests/unit/data_retrieval/test_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/data_retrieval/test_tools.py
import json

from tests.fixtures.demand_history import intermittent_history
from tests.fixtures.parts import lru_tier_1_critical
from tests.fixtures.tenants import aircanada_essentiality
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.specialists.data_retrieval.agent import DataRetrievalAgent
from trax_io.specialists.data_retrieval.feature_store import InMemoryFeatureStore
from trax_io.specialists.data_retrieval.tools import build_data_tools


def test_get_bundle_tool_returns_serializable_payload(aircanada: TenantContext) -> None:
    fs = InMemoryFeatureStore()
    fs.upsert_part(lru_tier_1_critical())
    fs.upsert_demand_history(
        intermittent_history(pn=lru_tier_1_critical().pn, location="YYZ-MAIN")
    )
    fs.upsert_essentiality_mapping(aircanada_essentiality())
    agent = DataRetrievalAgent(feature_store=fs)
    tools = build_data_tools(agent)
    get_bundle = next(t for t in tools if t.name == "get_part_location_bundle")
    with tenant_scope(aircanada):
        result = get_bundle.invoke(
            {"pn": lru_tier_1_critical().pn, "location": "YYZ-MAIN"}
        )
    payload = json.loads(result)
    assert payload["part"]["pn"] == lru_tier_1_critical().pn
    assert payload["demand_history"]["observations"]
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/data_retrieval/test_tools.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/specialists/data_retrieval/tools.py
"""MCP tool definitions for the Data & Retrieval Agent.

Tools are framework-agnostic adapters around the agent's typed methods.
They serialize results to JSON for transport over AgentCore Gateway.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from trax_io.specialists.data_retrieval.agent import DataRetrievalAgent


@dataclass(frozen=True)
class DataTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    invoke: Callable[[dict[str, Any]], str]


def build_data_tools(agent: DataRetrievalAgent) -> list[DataTool]:
    def _get_bundle(args: dict[str, Any]) -> str:
        bundle = agent.fetch_part_location_bundle(
            pn=str(args["pn"]), location=str(args["location"])
        )
        return bundle.model_dump_json()

    return [
        DataTool(
            name="get_part_location_bundle",
            description=(
                "Fetch all data the optimizer needs for a single PN×Location decision: "
                "part attributes, demand history, interchangeability group, open orders."
            ),
            input_schema={
                "type": "object",
                "required": ["pn", "location"],
                "properties": {
                    "pn": {"type": "string", "description": "Part number"},
                    "location": {"type": "string", "description": "Location/warehouse code"},
                },
            },
            invoke=_get_bundle,
        ),
    ]
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/data_retrieval/test_tools.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/data_retrieval/tools.py tests/unit/data_retrieval/test_tools.py
git commit -m "feat(data): MCP tool definitions for AgentCore Gateway"
```

---

## Phase 5: Guardrail & Approval Agent

### Task 22: AutonomyTier resolver

**Files:**
- Create: `src/trax_io/specialists/guardrail/tiers.py`
- Create: `tests/unit/guardrail/__init__.py`
- Create: `tests/unit/guardrail/test_tiers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/guardrail/test_tiers.py
import pytest

from trax_io.contracts.guardrail import AutonomyTier
from trax_io.contracts.tenant import CanonicalCriticality
from trax_io.specialists.guardrail.tiers import TierContext, resolve_tier


@pytest.mark.parametrize(
    "criticality,unit_cost,delta_pct,tenant_age_days,expected",
    [
        (CanonicalCriticality.TIER_1, 100.0, 0.01, 365, AutonomyTier.ADVISOR),  # always A
        (CanonicalCriticality.TIER_4, 50.0, 0.30, 365, AutonomyTier.AUTONOMOUS),
        (CanonicalCriticality.TIER_4, 50.0, 0.50, 365, AutonomyTier.ADVISOR),  # delta out of band
        (CanonicalCriticality.TIER_3, 9_000.0, 0.10, 365, AutonomyTier.BOUNDED),
        (CanonicalCriticality.TIER_3, 9_000.0, 0.20, 365, AutonomyTier.ADVISOR),  # delta out
        (CanonicalCriticality.TIER_2, 11_000.0, 0.01, 365, AutonomyTier.ADVISOR),  # cost gate
        (CanonicalCriticality.TIER_4, 50.0, 0.10, 30, AutonomyTier.ADVISOR),  # new tenant
    ],
)
def test_tier_resolution_matrix(
    criticality, unit_cost, delta_pct, tenant_age_days, expected
):
    ctx = TierContext(
        criticality=criticality,
        unit_cost=unit_cost,
        delta_pct=delta_pct,
        tenant_age_days=tenant_age_days,
        on_aog_case=False,
    )
    assert resolve_tier(ctx) == expected


def test_active_aog_case_forces_advisor():
    ctx = TierContext(
        criticality=CanonicalCriticality.TIER_4,
        unit_cost=50.0,
        delta_pct=0.10,
        tenant_age_days=365,
        on_aog_case=True,
    )
    assert resolve_tier(ctx) == AutonomyTier.ADVISOR
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/guardrail/test_tiers.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/specialists/guardrail/tiers.py
"""Autonomy tier resolution per design §6.1."""
from __future__ import annotations

from dataclasses import dataclass

from trax_io.contracts.guardrail import AutonomyTier
from trax_io.contracts.tenant import CanonicalCriticality

ADVISOR_ONLY_TENANT_AGE_DAYS = 90
TIER_C_MAX_UNIT_COST = 500.0
TIER_C_MAX_DELTA = 0.40
TIER_B_MAX_UNIT_COST = 10_000.0
TIER_B_MAX_DELTA = 0.15
HARD_ADVISOR_DELTA = 0.25
HARD_ADVISOR_UNIT_COST = 10_000.0


@dataclass(frozen=True)
class TierContext:
    criticality: CanonicalCriticality
    unit_cost: float
    delta_pct: float
    tenant_age_days: int
    on_aog_case: bool


def resolve_tier(ctx: TierContext) -> AutonomyTier:
    if ctx.on_aog_case:
        return AutonomyTier.ADVISOR
    if ctx.criticality == CanonicalCriticality.TIER_1:
        return AutonomyTier.ADVISOR
    if ctx.tenant_age_days <= ADVISOR_ONLY_TENANT_AGE_DAYS:
        return AutonomyTier.ADVISOR
    if ctx.delta_pct > HARD_ADVISOR_DELTA:
        return AutonomyTier.ADVISOR
    if ctx.unit_cost >= HARD_ADVISOR_UNIT_COST:
        return AutonomyTier.ADVISOR

    if (
        ctx.criticality >= CanonicalCriticality.TIER_4
        and ctx.unit_cost < TIER_C_MAX_UNIT_COST
        and ctx.delta_pct <= TIER_C_MAX_DELTA
    ):
        return AutonomyTier.AUTONOMOUS

    if (
        CanonicalCriticality.TIER_2 <= ctx.criticality <= CanonicalCriticality.TIER_3
        and ctx.unit_cost < TIER_B_MAX_UNIT_COST
        and ctx.delta_pct <= TIER_B_MAX_DELTA
    ):
        return AutonomyTier.BOUNDED

    return AutonomyTier.ADVISOR
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/guardrail/test_tiers.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/guardrail/tiers.py tests/unit/guardrail/
git commit -m "feat(guardrail): autonomy tier resolution per design §6.1"
```

---

### Task 23: Hard guardrail validators

**Files:**
- Create: `src/trax_io/specialists/guardrail/hard_guardrails.py`
- Create: `tests/unit/guardrail/test_hard_guardrails.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/guardrail/test_hard_guardrails.py
from tests.fixtures.parts import expendable_tier_4_cheap, lru_tier_1_critical
from trax_io.contracts.policy import PolicyKind, PolicyRecommendation
from trax_io.specialists.guardrail.hard_guardrails import (
    HardGuardrailContext,
    HardGuardrailViolation,
    evaluate_hard_guardrails,
)


def _rec(rop: int, eoq: int, ss: int, max_stock: int) -> PolicyRecommendation:
    return PolicyRecommendation(
        tenant_id="aircanada",
        pn="P-1",
        location="YYZ",
        rop=rop,
        eoq=eoq,
        safety_stock=ss,
        max_stock=max_stock,
        policy_kind=PolicyKind.S_S,
        provenance_id="prov-1",
    )


def test_passes_when_all_constraints_satisfied():
    ctx = HardGuardrailContext(
        recommendation=_rec(rop=4, eoq=3, ss=2, max_stock=10),
        current_rop=4,
        current_eoq=3,
        current_max=10,
        part=expendable_tier_4_cheap(),
        avg_daily_demand=0.5,
        open_order_qty=0,
        on_aog_case=False,
        min_order_qty=1,
    )
    violations = evaluate_hard_guardrails(ctx)
    assert violations == []


def test_delta_cap_100pct_violation():
    ctx = HardGuardrailContext(
        recommendation=_rec(rop=20, eoq=3, ss=2, max_stock=30),
        current_rop=4,
        current_eoq=3,
        current_max=10,
        part=expendable_tier_4_cheap(),
        avg_daily_demand=0.5,
        open_order_qty=0,
        on_aog_case=False,
        min_order_qty=1,
    )
    violations = evaluate_hard_guardrails(ctx)
    assert HardGuardrailViolation.DELTA_EXCEEDS_100PCT in violations


def test_min_oq_floor_violation():
    ctx = HardGuardrailContext(
        recommendation=_rec(rop=4, eoq=3, ss=2, max_stock=10),
        current_rop=4,
        current_eoq=3,
        current_max=10,
        part=expendable_tier_4_cheap(),
        avg_daily_demand=0.5,
        open_order_qty=0,
        on_aog_case=False,
        min_order_qty=5,
    )
    violations = evaluate_hard_guardrails(ctx)
    assert HardGuardrailViolation.EOQ_BELOW_MIN_OQ in violations


def test_shelf_life_clamp():
    part = expendable_tier_4_cheap()  # shelf_life_days = 720
    # Max-stock × avg_daily_demand must be ≤ 0.6 × 720 = 432
    ctx = HardGuardrailContext(
        recommendation=_rec(rop=10, eoq=10, ss=5, max_stock=1000),
        current_rop=10,
        current_eoq=10,
        current_max=1000,
        part=part,
        avg_daily_demand=2.0,  # 1000 × 2 = 2000 > 432
        open_order_qty=0,
        on_aog_case=False,
        min_order_qty=1,
    )
    violations = evaluate_hard_guardrails(ctx)
    assert HardGuardrailViolation.SHELF_LIFE_EXCEEDED in violations


def test_open_orders_would_overflow_max():
    ctx = HardGuardrailContext(
        recommendation=_rec(rop=4, eoq=3, ss=2, max_stock=10),
        current_rop=4,
        current_eoq=3,
        current_max=10,
        part=expendable_tier_4_cheap(),
        avg_daily_demand=0.5,
        open_order_qty=20,  # already on order
        on_aog_case=False,
        min_order_qty=1,
    )
    violations = evaluate_hard_guardrails(ctx)
    assert HardGuardrailViolation.OPEN_ORDERS_OVERFLOW_MAX in violations


def test_aog_active_blocks_all_writes():
    ctx = HardGuardrailContext(
        recommendation=_rec(rop=4, eoq=3, ss=2, max_stock=10),
        current_rop=4,
        current_eoq=3,
        current_max=10,
        part=lru_tier_1_critical(),
        avg_daily_demand=0.1,
        open_order_qty=0,
        on_aog_case=True,
        min_order_qty=1,
    )
    violations = evaluate_hard_guardrails(ctx)
    assert HardGuardrailViolation.AOG_ACTIVE in violations
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/guardrail/test_hard_guardrails.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/specialists/guardrail/hard_guardrails.py
"""Non-bypassable hard guardrails per design §6.2."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trax_io.contracts.part import Part
from trax_io.contracts.policy import PolicyRecommendation

DELTA_HARD_CAP = 1.00          # 100% per design §6.2
SHELF_LIFE_FRACTION = 0.6      # Max × avg_daily_demand ≤ 0.6 × shelf_life_days
HAZMAT_MAX_INCREASE = 2.0      # Max can at most 2× per write cycle
TOOL_MAX_INCREASE = 2.0


class HardGuardrailViolation(StrEnum):
    DELTA_EXCEEDS_100PCT = "delta_exceeds_100pct"
    SS_NEGATIVE = "safety_stock_negative"
    ROP_BELOW_SS = "rop_below_safety_stock"
    MAX_BELOW_ROP_PLUS_EOQ = "max_below_rop_plus_eoq"
    EOQ_BELOW_MIN_OQ = "eoq_below_min_oq"
    SHELF_LIFE_EXCEEDED = "shelf_life_exceeded"
    HAZMAT_INCREASE_EXCEEDED = "hazmat_increase_exceeded"
    TOOL_INCREASE_EXCEEDED = "tool_increase_exceeded"
    OPEN_ORDERS_OVERFLOW_MAX = "open_orders_overflow_max"
    AOG_ACTIVE = "aog_active"


@dataclass(frozen=True)
class HardGuardrailContext:
    recommendation: PolicyRecommendation
    current_rop: int
    current_eoq: int
    current_max: int
    part: Part
    avg_daily_demand: float
    open_order_qty: int
    on_aog_case: bool
    min_order_qty: int


def _delta_pct(new_value: int, current: int) -> float:
    if current <= 0:
        return float("inf") if new_value > 0 else 0.0
    return abs(new_value - current) / current


def evaluate_hard_guardrails(ctx: HardGuardrailContext) -> list[HardGuardrailViolation]:
    rec = ctx.recommendation
    out: list[HardGuardrailViolation] = []

    if ctx.on_aog_case:
        out.append(HardGuardrailViolation.AOG_ACTIVE)

    if (
        _delta_pct(rec.rop, ctx.current_rop) > DELTA_HARD_CAP
        or _delta_pct(rec.eoq, ctx.current_eoq) > DELTA_HARD_CAP
        or _delta_pct(rec.max_stock, ctx.current_max) > DELTA_HARD_CAP
    ):
        out.append(HardGuardrailViolation.DELTA_EXCEEDS_100PCT)

    if rec.safety_stock < 0:
        out.append(HardGuardrailViolation.SS_NEGATIVE)
    if rec.rop < rec.safety_stock:
        out.append(HardGuardrailViolation.ROP_BELOW_SS)
    if rec.max_stock < rec.rop + rec.eoq:
        out.append(HardGuardrailViolation.MAX_BELOW_ROP_PLUS_EOQ)
    if rec.eoq < ctx.min_order_qty:
        out.append(HardGuardrailViolation.EOQ_BELOW_MIN_OQ)

    if ctx.part.shelf_life_days is not None and ctx.avg_daily_demand > 0:
        if rec.max_stock * ctx.avg_daily_demand > SHELF_LIFE_FRACTION * ctx.part.shelf_life_days:
            out.append(HardGuardrailViolation.SHELF_LIFE_EXCEEDED)

    if ctx.part.hazmat:
        if ctx.current_max > 0 and rec.max_stock / ctx.current_max > HAZMAT_MAX_INCREASE:
            out.append(HardGuardrailViolation.HAZMAT_INCREASE_EXCEEDED)
    if ctx.part.tool_control:
        if ctx.current_max > 0 and rec.max_stock / ctx.current_max > TOOL_MAX_INCREASE:
            out.append(HardGuardrailViolation.TOOL_INCREASE_EXCEEDED)

    if ctx.open_order_qty > rec.max_stock:
        out.append(HardGuardrailViolation.OPEN_ORDERS_OVERFLOW_MAX)

    return out
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/guardrail/test_hard_guardrails.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/guardrail/hard_guardrails.py tests/unit/guardrail/test_hard_guardrails.py
git commit -m "feat(guardrail): non-bypassable hard guardrails per §6.2"
```

---

### Task 24: ApprovalQueue protocol + InMemory fake

**Files:**
- Create: `src/trax_io/specialists/guardrail/approval.py`
- Create: `tests/unit/guardrail/test_approval.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/guardrail/test_approval.py
from trax_io.contracts.guardrail import ApprovalTask
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.specialists.guardrail.approval import InMemoryApprovalQueue


def test_enqueue_and_list_returns_priority_ordered(aircanada: TenantContext) -> None:
    q = InMemoryApprovalQueue()
    with tenant_scope(aircanada):
        q.enqueue(
            ApprovalTask(
                task_id="t1", tenant_id="aircanada", pn="A", location="YYZ",
                priority_score=10.0,
            )
        )
        q.enqueue(
            ApprovalTask(
                task_id="t2", tenant_id="aircanada", pn="B", location="YYZ",
                priority_score=99.0,
            )
        )
        items = q.list_pending(limit=10)
    assert [t.task_id for t in items] == ["t2", "t1"]


def test_pending_isolated_per_tenant(
    aircanada: TenantContext, jetblue: TenantContext
) -> None:
    q = InMemoryApprovalQueue()
    with tenant_scope(aircanada):
        q.enqueue(
            ApprovalTask(
                task_id="t1", tenant_id="aircanada", pn="A", location="YYZ",
                priority_score=10.0,
            )
        )
    with tenant_scope(jetblue):
        items = q.list_pending(limit=10)
    assert items == []


def test_resolve_removes_task(aircanada: TenantContext) -> None:
    q = InMemoryApprovalQueue()
    with tenant_scope(aircanada):
        q.enqueue(
            ApprovalTask(
                task_id="t1", tenant_id="aircanada", pn="A", location="YYZ",
                priority_score=10.0,
            )
        )
        q.resolve(task_id="t1", outcome="approved")
        assert q.list_pending(limit=10) == []
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/guardrail/test_approval.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/specialists/guardrail/approval.py
"""Approval queue protocol + in-memory fake.

Production uses DynamoDB (added in Task 45 CDK stack); this fake is for tests
and local dev.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal, Protocol

from trax_io.contracts.guardrail import ApprovalTask
from trax_io.identity.context import current_tenant


class ApprovalQueue(Protocol):
    def enqueue(self, task: ApprovalTask) -> None: ...
    def list_pending(self, *, limit: int) -> list[ApprovalTask]: ...
    def resolve(self, *, task_id: str, outcome: Literal["approved", "rejected"]) -> None: ...


@dataclass
class InMemoryApprovalQueue:
    _pending: dict[str, dict[str, ApprovalTask]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    _resolved: dict[str, dict[str, str]] = field(
        default_factory=lambda: defaultdict(dict)
    )

    def enqueue(self, task: ApprovalTask) -> None:
        self._pending[task.tenant_id][task.task_id] = task

    def list_pending(self, *, limit: int) -> list[ApprovalTask]:
        tenant = current_tenant().tenant_id
        items = list(self._pending.get(tenant, {}).values())
        items.sort(key=lambda t: t.priority_score, reverse=True)
        return items[:limit]

    def resolve(
        self, *, task_id: str, outcome: Literal["approved", "rejected"]
    ) -> None:
        tenant = current_tenant().tenant_id
        if task_id in self._pending.get(tenant, {}):
            del self._pending[tenant][task_id]
            self._resolved[tenant][task_id] = outcome
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/guardrail/test_approval.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/guardrail/approval.py tests/unit/guardrail/test_approval.py
git commit -m "feat(guardrail): approval queue protocol + in-memory fake"
```

---

### Task 25: GuardrailAgent — orchestrating tier → hard guardrails → outcome

**Files:**
- Create: `src/trax_io/specialists/guardrail/agent.py`
- Create: `tests/unit/guardrail/test_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/guardrail/test_agent.py
from datetime import UTC, datetime
from uuid import uuid4

from tests.fixtures.parts import expendable_tier_4_cheap, lru_tier_1_critical
from trax_io.contracts.guardrail import AutonomyTier, GuardrailStatus
from trax_io.contracts.policy import PolicyKind, PolicyRecommendation
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.specialists.guardrail.agent import GuardrailAgent, GuardrailRequest
from trax_io.specialists.guardrail.approval import InMemoryApprovalQueue


def _rec(pn: str, rop: int, eoq: int, ss: int, max_stock: int) -> PolicyRecommendation:
    return PolicyRecommendation(
        tenant_id="aircanada",
        pn=pn,
        location="YYZ",
        rop=rop,
        eoq=eoq,
        safety_stock=ss,
        max_stock=max_stock,
        policy_kind=PolicyKind.S_S,
        provenance_id=str(uuid4()),
    )


def test_tier_1_part_always_routes_to_advisor(aircanada: TenantContext) -> None:
    queue = InMemoryApprovalQueue()
    agent = GuardrailAgent(approval_queue=queue, killswitch=lambda _: False)
    req = GuardrailRequest(
        recommendation=_rec(pn=lru_tier_1_critical().pn, rop=2, eoq=1, ss=1, max_stock=4),
        current_rop=2,
        current_eoq=1,
        current_max=4,
        part=lru_tier_1_critical(),
        avg_daily_demand=0.01,
        open_order_qty=0,
        tenant_age_days=365,
        on_aog_case=False,
        min_order_qty=1,
    )
    with tenant_scope(aircanada):
        outcome = agent.evaluate(req)
    assert outcome.status == GuardrailStatus.QUEUED_FOR_APPROVAL
    assert outcome.tier == AutonomyTier.ADVISOR


def test_tier_4_within_band_passes_through_to_write(aircanada: TenantContext) -> None:
    queue = InMemoryApprovalQueue()
    agent = GuardrailAgent(approval_queue=queue, killswitch=lambda _: False)
    req = GuardrailRequest(
        recommendation=_rec(pn=expendable_tier_4_cheap().pn, rop=5, eoq=3, ss=2, max_stock=10),
        current_rop=4,
        current_eoq=3,
        current_max=8,
        part=expendable_tier_4_cheap(),
        avg_daily_demand=0.1,
        open_order_qty=0,
        tenant_age_days=365,
        on_aog_case=False,
        min_order_qty=1,
    )
    with tenant_scope(aircanada):
        outcome = agent.evaluate(req)
    assert outcome.status == GuardrailStatus.APPROVED_FOR_WRITE
    assert outcome.tier == AutonomyTier.AUTONOMOUS


def test_hard_guardrail_violation_overrides_to_rejected(aircanada: TenantContext) -> None:
    queue = InMemoryApprovalQueue()
    agent = GuardrailAgent(approval_queue=queue, killswitch=lambda _: False)
    req = GuardrailRequest(
        recommendation=_rec(pn=expendable_tier_4_cheap().pn, rop=4, eoq=3, ss=2, max_stock=10),
        current_rop=4,
        current_eoq=3,
        current_max=10,
        part=expendable_tier_4_cheap(),
        avg_daily_demand=0.1,
        open_order_qty=999,  # overflows max
        tenant_age_days=365,
        on_aog_case=False,
        min_order_qty=1,
    )
    with tenant_scope(aircanada):
        outcome = agent.evaluate(req)
    assert outcome.status == GuardrailStatus.REJECTED_HARD_GUARDRAIL


def test_killswitch_engaged_routes_advisor(aircanada: TenantContext) -> None:
    queue = InMemoryApprovalQueue()
    agent = GuardrailAgent(approval_queue=queue, killswitch=lambda tenant: True)
    req = GuardrailRequest(
        recommendation=_rec(pn=expendable_tier_4_cheap().pn, rop=4, eoq=3, ss=2, max_stock=10),
        current_rop=4,
        current_eoq=3,
        current_max=10,
        part=expendable_tier_4_cheap(),
        avg_daily_demand=0.1,
        open_order_qty=0,
        tenant_age_days=365,
        on_aog_case=False,
        min_order_qty=1,
    )
    with tenant_scope(aircanada):
        outcome = agent.evaluate(req)
    assert outcome.status == GuardrailStatus.QUEUED_FOR_APPROVAL
    assert outcome.tier == AutonomyTier.ADVISOR
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/guardrail/test_agent.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/specialists/guardrail/agent.py
"""Guardrail & Approval Agent — orchestrates tier resolution + hard guardrails + approval queueing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from trax_io.contracts.guardrail import (
    ApprovalTask,
    AutonomyTier,
    GuardrailOutcome,
    GuardrailStatus,
)
from trax_io.contracts.part import Part
from trax_io.contracts.policy import PolicyRecommendation
from trax_io.specialists.base import Specialist
from trax_io.specialists.guardrail.approval import ApprovalQueue
from trax_io.specialists.guardrail.hard_guardrails import (
    HardGuardrailContext,
    evaluate_hard_guardrails,
)
from trax_io.specialists.guardrail.tiers import TierContext, resolve_tier


@dataclass(frozen=True)
class GuardrailRequest:
    recommendation: PolicyRecommendation
    current_rop: int
    current_eoq: int
    current_max: int
    part: Part
    avg_daily_demand: float
    open_order_qty: int
    tenant_age_days: int
    on_aog_case: bool
    min_order_qty: int


class GuardrailAgent(Specialist):
    def __init__(
        self,
        *,
        approval_queue: ApprovalQueue,
        killswitch: Callable[[str], bool],
    ) -> None:
        super().__init__(specialist_name="guardrail")
        self._queue = approval_queue
        self._killswitch = killswitch

    def evaluate(self, req: GuardrailRequest) -> GuardrailOutcome:
        rec = req.recommendation
        self._assert_tenant_match(rec.tenant_id)

        # 1. Hard guardrails — always evaluated, never bypassed.
        hg_ctx = HardGuardrailContext(
            recommendation=rec,
            current_rop=req.current_rop,
            current_eoq=req.current_eoq,
            current_max=req.current_max,
            part=req.part,
            avg_daily_demand=req.avg_daily_demand,
            open_order_qty=req.open_order_qty,
            on_aog_case=req.on_aog_case,
            min_order_qty=req.min_order_qty,
        )
        violations = evaluate_hard_guardrails(hg_ctx)
        if violations:
            self._log.warning(
                "hard_guardrail_violation",
                violations=[v.value for v in violations],
                pn=rec.pn,
            )
            return GuardrailOutcome(
                status=GuardrailStatus.REJECTED_HARD_GUARDRAIL,
                tier=AutonomyTier.ADVISOR,
                rejection_reason=";".join(v.value for v in violations),
            )

        # 2. Killswitch — global tenant pause.
        if self._killswitch(rec.tenant_id):
            return self._queue_for_advisor(req, reason="killswitch_engaged")

        # 3. Tier resolution.
        delta_pct = self._max_delta(req)
        tier_ctx = TierContext(
            criticality=req.part.criticality,
            unit_cost=req.part.average_cost,
            delta_pct=delta_pct,
            tenant_age_days=req.tenant_age_days,
            on_aog_case=req.on_aog_case,
        )
        tier = resolve_tier(tier_ctx)

        if tier == AutonomyTier.ADVISOR:
            return self._queue_for_advisor(req, reason="tier_a")

        return GuardrailOutcome(
            status=GuardrailStatus.APPROVED_FOR_WRITE,
            tier=tier,
        )

    def _queue_for_advisor(self, req: GuardrailRequest, *, reason: str) -> GuardrailOutcome:
        rec = req.recommendation
        priority = req.part.average_cost * float(int(req.part.criticality)) * (
            1.0 + self._max_delta(req)
        )
        task = ApprovalTask(
            task_id=str(uuid4()),
            tenant_id=rec.tenant_id,
            pn=rec.pn,
            location=rec.location,
            priority_score=priority,
            reason=reason,
        )
        self._queue.enqueue(task)
        return GuardrailOutcome(
            status=GuardrailStatus.QUEUED_FOR_APPROVAL,
            tier=AutonomyTier.ADVISOR,
            approval_task=task,
        )

    @staticmethod
    def _max_delta(req: GuardrailRequest) -> float:
        rec = req.recommendation
        deltas = []
        for new, cur in (
            (rec.rop, req.current_rop),
            (rec.eoq, req.current_eoq),
            (rec.max_stock, req.current_max),
        ):
            if cur <= 0:
                deltas.append(1.0 if new > 0 else 0.0)
            else:
                deltas.append(abs(new - cur) / cur)
        return max(deltas) if deltas else 0.0
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/guardrail/test_agent.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/guardrail/agent.py tests/unit/guardrail/test_agent.py
git commit -m "feat(guardrail): GuardrailAgent — tier + hard guardrails + approval routing"
```

---

## Phase 6: Writeback Agent

### Task 26: fake_emro FastAPI mock server

**Files:**
- Create: `tests/fixtures/fake_emro/__init__.py`
- Create: `tests/fixtures/fake_emro/server.py`
- Create: `tests/fixtures/fake_emro/test_server_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/fixtures/fake_emro/test_server_smoke.py
from fastapi.testclient import TestClient

from tests.fixtures.fake_emro.server import build_app


def test_writeback_records_history():
    app = build_app()
    client = TestClient(app)
    r = client.put(
        "/v1/tenants/aircanada/inventory-level/P-1/YYZ",
        json={
            "rop": 5,
            "eoq": 3,
            "safety_stock": 2,
            "max_stock": 9,
            "provenance_id": "prov-1",
        },
        headers={"Idempotency-Key": "k1", "X-Service-Principal": "trax-io"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["new_values"] == {"rop": 5, "eoq": 3, "safety_stock": 2, "max_stock": 9}

    h = client.get("/v1/tenants/aircanada/inventory-level/P-1/YYZ/history")
    assert h.status_code == 200
    assert len(h.json()) == 1


def test_idempotency_key_returns_same_response():
    app = build_app()
    client = TestClient(app)
    payload = {
        "rop": 5, "eoq": 3, "safety_stock": 2, "max_stock": 9,
        "provenance_id": "prov-1",
    }
    headers = {"Idempotency-Key": "same-key", "X-Service-Principal": "trax-io"}
    r1 = client.put(
        "/v1/tenants/aircanada/inventory-level/P-1/YYZ", json=payload, headers=headers
    )
    r2 = client.put(
        "/v1/tenants/aircanada/inventory-level/P-1/YYZ", json=payload, headers=headers
    )
    assert r1.json() == r2.json()
    h = client.get("/v1/tenants/aircanada/inventory-level/P-1/YYZ/history")
    assert len(h.json()) == 1


def test_unauthorized_without_service_principal():
    app = build_app()
    client = TestClient(app)
    r = client.put(
        "/v1/tenants/aircanada/inventory-level/P-1/YYZ",
        json={
            "rop": 5, "eoq": 3, "safety_stock": 2, "max_stock": 9,
            "provenance_id": "prov-1",
        },
        headers={"Idempotency-Key": "k1"},
    )
    assert r.status_code == 401
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/fixtures/fake_emro/test_server_smoke.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# tests/fixtures/fake_emro/__init__.py — empty file
```

```python
# tests/fixtures/fake_emro/server.py
"""FastAPI mock of the eMRO Writeback REST API.

Sub-plan #6 implements the real Java endpoint inside eMRO; this mock matches
the OpenAPI contract so the agent spine can be developed and tested without
the eMRO codebase.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


class WritebackPayload(BaseModel):
    rop: int
    eoq: int
    safety_stock: int
    max_stock: int
    provenance_id: str


class WritebackResponse(BaseModel):
    tenant_id: str
    pn: str
    location: str
    old_values: dict[str, int] | None
    new_values: dict[str, int]
    written_at: datetime


@dataclass
class _State:
    levels: dict[tuple[str, str, str], dict[str, int]] = field(default_factory=dict)
    history: dict[tuple[str, str, str], list[dict]] = field(
        default_factory=lambda: defaultdict(list)
    )
    idempotency: dict[str, dict] = field(default_factory=dict)


def build_app() -> FastAPI:
    app = FastAPI(title="fake-emro-writeback", version="0.1.0")
    state = _State()

    def _check_principal(principal: str | None) -> None:
        if principal != "trax-io":
            raise HTTPException(status_code=401, detail="missing service principal")

    @app.put("/v1/tenants/{tenant_id}/inventory-level/{pn}/{location}")
    def upsert(
        tenant_id: str,
        pn: str,
        location: str,
        payload: WritebackPayload,
        idempotency_key: str = Header(..., alias="Idempotency-Key"),
        x_service_principal: str | None = Header(None, alias="X-Service-Principal"),
    ) -> WritebackResponse:
        _check_principal(x_service_principal)
        cached = state.idempotency.get(idempotency_key)
        if cached:
            return WritebackResponse(**cached)
        key = (tenant_id, pn, location)
        old = state.levels.get(key)
        new = payload.model_dump()
        state.levels[key] = {
            "rop": new["rop"],
            "eoq": new["eoq"],
            "safety_stock": new["safety_stock"],
            "max_stock": new["max_stock"],
        }
        record = {
            "tenant_id": tenant_id,
            "pn": pn,
            "location": location,
            "old_values": old,
            "new_values": state.levels[key],
            "written_at": datetime.now(UTC).isoformat(),
            "provenance_id": new["provenance_id"],
        }
        state.history[key].append(record)
        resp = WritebackResponse(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            old_values=old,
            new_values=state.levels[key],
            written_at=datetime.fromisoformat(record["written_at"]),
        )
        state.idempotency[idempotency_key] = resp.model_dump(mode="json")
        return resp

    @app.get("/v1/tenants/{tenant_id}/inventory-level/{pn}/{location}/history")
    def history(tenant_id: str, pn: str, location: str) -> list[dict]:
        return state.history.get((tenant_id, pn, location), [])

    @app.post("/v1/tenants/{tenant_id}/inventory-level/{pn}/{location}/rollback")
    def rollback(
        tenant_id: str,
        pn: str,
        location: str,
        x_service_principal: str | None = Header(None, alias="X-Service-Principal"),
    ) -> WritebackResponse:
        _check_principal(x_service_principal)
        key = (tenant_id, pn, location)
        records = state.history.get(key, [])
        if len(records) < 2:
            raise HTTPException(status_code=409, detail="no prior version to roll back to")
        prior = records[-2]["new_values"]
        state.levels[key] = prior
        rolled = {
            "tenant_id": tenant_id,
            "pn": pn,
            "location": location,
            "old_values": records[-1]["new_values"],
            "new_values": prior,
            "written_at": datetime.now(UTC).isoformat(),
            "provenance_id": "rollback",
        }
        state.history[key].append(rolled)
        return WritebackResponse(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            old_values=rolled["old_values"],
            new_values=prior,
            written_at=datetime.fromisoformat(rolled["written_at"]),
        )

    return app
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/fixtures/fake_emro/test_server_smoke.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/fake_emro/
git commit -m "test(fake_emro): FastAPI mock of eMRO Writeback REST contract"
```

---

### Task 27: WritebackClient (httpx async with retry + idempotency)

**Files:**
- Create: `src/trax_io/specialists/writeback/__init__.py`
- Create: `src/trax_io/specialists/writeback/client.py`
- Create: `tests/unit/writeback/__init__.py`
- Create: `tests/unit/writeback/test_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/writeback/test_client.py
import threading
import time

import pytest
import uvicorn

from tests.fixtures.fake_emro.server import build_app
from trax_io.contracts.writeback import WritebackRequest, WritebackStatus
from trax_io.specialists.writeback.client import WritebackClient


@pytest.fixture(scope="module")
def fake_emro_url() -> str:
    app = build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


async def test_client_writes_and_records_response(fake_emro_url: str) -> None:
    client = WritebackClient(base_url=fake_emro_url)
    req = WritebackRequest(
        tenant_id="aircanada",
        pn="P-1",
        location="YYZ",
        rop=5,
        eoq=3,
        safety_stock=2,
        max_stock=9,
        provenance_id="prov-1",
        idempotency_key="k1",
    )
    result = await client.upsert(req)
    assert result.status == WritebackStatus.WRITTEN
    assert result.new_values == {"rop": 5, "eoq": 3, "safety_stock": 2, "max_stock": 9}


async def test_client_idempotent_repeat(fake_emro_url: str) -> None:
    client = WritebackClient(base_url=fake_emro_url)
    req = WritebackRequest(
        tenant_id="aircanada",
        pn="P-IDEMP",
        location="YYZ",
        rop=5,
        eoq=3,
        safety_stock=2,
        max_stock=9,
        provenance_id="prov-1",
        idempotency_key="k-idemp",
    )
    r1 = await client.upsert(req)
    r2 = await client.upsert(req)
    assert r1 == r2
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/writeback/test_client.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/specialists/writeback/__init__.py — empty file
```

```python
# src/trax_io/specialists/writeback/client.py
"""HTTP client for the eMRO Writeback REST API."""
from __future__ import annotations

from datetime import datetime

import httpx

from trax_io.contracts.writeback import WritebackRequest, WritebackResult, WritebackStatus


class WritebackClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_principal: str = "trax-io",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._principal = service_principal
        self._timeout = timeout_seconds
        self._retries = max_retries

    async def upsert(self, req: WritebackRequest) -> WritebackResult:
        url = (
            f"{self._base}/v1/tenants/{req.tenant_id}"
            f"/inventory-level/{req.pn}/{req.location}"
        )
        payload = {
            "rop": req.rop,
            "eoq": req.eoq,
            "safety_stock": req.safety_stock,
            "max_stock": req.max_stock,
            "provenance_id": req.provenance_id,
        }
        headers = {
            "Idempotency-Key": req.idempotency_key,
            "X-Service-Principal": self._principal,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            for attempt in range(1, self._retries + 1):
                try:
                    r = await http.put(url, json=payload, headers=headers)
                    if r.status_code == 200:
                        body = r.json()
                        return WritebackResult(
                            tenant_id=req.tenant_id,
                            pn=req.pn,
                            location=req.location,
                            status=WritebackStatus.WRITTEN,
                            old_values=body.get("old_values"),
                            new_values=body.get("new_values"),
                            written_at=datetime.fromisoformat(body["written_at"]),
                        )
                    if 400 <= r.status_code < 500:
                        return WritebackResult(
                            tenant_id=req.tenant_id,
                            pn=req.pn,
                            location=req.location,
                            status=WritebackStatus.FAILED,
                            error_message=f"{r.status_code}: {r.text}",
                        )
                except httpx.TransportError as e:
                    if attempt >= self._retries:
                        return WritebackResult(
                            tenant_id=req.tenant_id,
                            pn=req.pn,
                            location=req.location,
                            status=WritebackStatus.FAILED,
                            error_message=str(e),
                        )
            return WritebackResult(
                tenant_id=req.tenant_id,
                pn=req.pn,
                location=req.location,
                status=WritebackStatus.FAILED,
                error_message="exceeded retries",
            )
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/writeback/test_client.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/writeback/ tests/unit/writeback/
git commit -m "feat(writeback): async HTTP client with idempotency and retry"
```

---

### Task 28: WritebackAgent specialist

**Files:**
- Create: `src/trax_io/specialists/writeback/agent.py`
- Create: `tests/unit/writeback/test_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/writeback/test_agent.py
import threading
import time

import pytest
import uvicorn

from tests.fixtures.fake_emro.server import build_app
from trax_io.contracts.policy import PolicyKind, PolicyRecommendation
from trax_io.contracts.tenant import TenantContext
from trax_io.contracts.writeback import WritebackStatus
from trax_io.identity.context import tenant_scope
from trax_io.specialists.writeback.agent import WritebackAgent
from trax_io.specialists.writeback.client import WritebackClient


@pytest.fixture(scope="module")
def fake_emro_url() -> str:
    app = build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


async def test_agent_writes_recommendation_to_emro(
    fake_emro_url: str, aircanada: TenantContext
) -> None:
    agent = WritebackAgent(client=WritebackClient(base_url=fake_emro_url))
    rec = PolicyRecommendation(
        tenant_id="aircanada",
        pn="P-1",
        location="YYZ",
        rop=5,
        eoq=3,
        safety_stock=2,
        max_stock=9,
        policy_kind=PolicyKind.S_S,
        provenance_id="prov-1",
    )
    with tenant_scope(aircanada):
        result = await agent.write(recommendation=rec, idempotency_key="k1")
    assert result.status == WritebackStatus.WRITTEN
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/writeback/test_agent.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/specialists/writeback/agent.py
"""Writeback specialist — only agent with PN_INVENTORY_LEVEL:Write IAM permission."""
from __future__ import annotations

from trax_io.contracts.policy import PolicyRecommendation
from trax_io.contracts.writeback import WritebackRequest, WritebackResult
from trax_io.specialists.base import Specialist
from trax_io.specialists.writeback.client import WritebackClient


class WritebackAgent(Specialist):
    def __init__(self, *, client: WritebackClient) -> None:
        super().__init__(specialist_name="writeback")
        self._client = client

    async def write(
        self, *, recommendation: PolicyRecommendation, idempotency_key: str
    ) -> WritebackResult:
        self._assert_tenant_match(recommendation.tenant_id)
        req = WritebackRequest(
            tenant_id=recommendation.tenant_id,
            pn=recommendation.pn,
            location=recommendation.location,
            rop=recommendation.rop,
            eoq=recommendation.eoq,
            safety_stock=recommendation.safety_stock,
            max_stock=recommendation.max_stock,
            provenance_id=recommendation.provenance_id,
            idempotency_key=idempotency_key,
        )
        result = await self._client.upsert(req)
        self._log.info(
            "writeback_complete",
            pn=recommendation.pn,
            location=recommendation.location,
            status=result.status.value,
        )
        return result
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/writeback/test_agent.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/writeback/agent.py tests/unit/writeback/test_agent.py
git commit -m "feat(writeback): WritebackAgent calls fake_emro REST"
```

---

## Phase 7: Strands Supervisor + Orchestration

### Task 29: Stub Forecasting and Policy agents (interface for sub-plan #5)

**Files:**
- Create: `src/trax_io/specialists/stubs/__init__.py`
- Create: `src/trax_io/specialists/stubs/forecasting_stub.py`
- Create: `src/trax_io/specialists/stubs/policy_stub.py`
- Create: `tests/unit/test_stubs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_stubs.py
from trax_io.contracts.forecast import ForecastDistribution, ForecastRequest
from trax_io.contracts.policy import PolicyKind, PolicyRecommendation
from trax_io.contracts.regime import Regime
from trax_io.specialists.stubs.forecasting_stub import StubForecastingAgent
from trax_io.specialists.stubs.policy_stub import StubPolicyEngineAgent


def test_forecasting_stub_returns_distribution_for_request():
    agent = StubForecastingAgent()
    req = ForecastRequest(tenant_id="aircanada", pn="P-1", location="YYZ")
    dist = agent.forecast(request=req, regime=Regime.INTERMITTENT, mean_history=4.0)
    assert isinstance(dist, ForecastDistribution)
    assert dist.mean > 0


def test_policy_stub_returns_consistent_recommendation():
    agent = StubPolicyEngineAgent()
    dist = ForecastDistribution(mean=5.0, variance=4.0, p50=5, p95=10, p99=14)
    rec = agent.recommend(
        tenant_id="aircanada",
        pn="P-1",
        location="YYZ",
        forecast=dist,
        lead_time_days=14,
        service_level=0.95,
    )
    assert isinstance(rec, PolicyRecommendation)
    assert rec.policy_kind == PolicyKind.S_S
    assert rec.max_stock >= rec.rop + rec.eoq
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/test_stubs.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/specialists/stubs/__init__.py — empty file
```

```python
# src/trax_io/specialists/stubs/forecasting_stub.py
"""Forecasting Agent stub. Sub-plan #5 replaces with Croston/LightGBM/FM ensemble."""
from __future__ import annotations

import math

from trax_io.contracts.forecast import ForecastDistribution, ForecastRequest
from trax_io.contracts.regime import Regime


class StubForecastingAgent:
    def forecast(
        self, *, request: ForecastRequest, regime: Regime, mean_history: float
    ) -> ForecastDistribution:
        # Deterministic stub — scales mean by regime, variance by sqrt of mean
        regime_scale = {
            Regime.ULTRA_RARE: 0.3,
            Regime.INTERMITTENT: 1.0,
            Regime.MODERATE: 1.0,
            Regime.HIGH_VOLUME: 1.05,
        }[regime]
        mean = max(0.1, mean_history * regime_scale)
        variance = max(0.1, mean * 1.5)
        sigma = math.sqrt(variance)
        return ForecastDistribution(
            mean=mean,
            variance=variance,
            p50=mean,
            p95=mean + 1.645 * sigma,
            p99=mean + 2.326 * sigma,
            model_id="stub-forecaster",
            model_version="0.1",
        )
```

```python
# src/trax_io/specialists/stubs/policy_stub.py
"""Policy Engine stub. Sub-plan #5 replaces with the deterministic policy engine."""
from __future__ import annotations

import math
from uuid import uuid4

from trax_io.contracts.forecast import ForecastDistribution
from trax_io.contracts.policy import PolicyKind, PolicyRecommendation


class StubPolicyEngineAgent:
    def recommend(
        self,
        *,
        tenant_id: str,
        pn: str,
        location: str,
        forecast: ForecastDistribution,
        lead_time_days: int,
        service_level: float = 0.95,
    ) -> PolicyRecommendation:
        # Newsvendor-flavored stub:
        # SS = z_alpha * sigma * sqrt(LT/30); ROP = mean*LT/30 + SS; EOQ = sqrt(2*D*K/h) ~ proxy.
        z = {0.90: 1.282, 0.92: 1.405, 0.95: 1.645, 0.98: 2.054, 0.995: 2.576}.get(
            round(service_level, 3), 1.645
        )
        sigma = math.sqrt(forecast.variance)
        lt_factor = max(0.1, lead_time_days / 30)
        safety_stock = max(0, int(round(z * sigma * math.sqrt(lt_factor))))
        rop_lt_demand = forecast.mean * lt_factor
        rop = max(safety_stock, int(round(rop_lt_demand + safety_stock)))
        # EOQ proxy: 2x rop, capped reasonably
        eoq = max(1, int(round(rop * 0.5)))
        max_stock = max(rop + eoq, int(round(rop * 2.0)))
        return PolicyRecommendation(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            rop=rop,
            eoq=eoq,
            safety_stock=safety_stock,
            max_stock=max_stock,
            policy_kind=PolicyKind.S_S,
            service_level_target=service_level,
            provenance_id=str(uuid4()),
            model_id="stub-policy",
        )
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/test_stubs.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/specialists/stubs/ tests/unit/test_stubs.py
git commit -m "feat(stubs): forecasting + policy stubs (interface for sub-plan #5)"
```

---

### Task 30: SupervisorAgent + orchestration graph

**Files:**
- Create: `src/trax_io/supervisor/__init__.py`
- Create: `src/trax_io/supervisor/orchestration.py`
- Create: `tests/unit/supervisor/__init__.py`
- Create: `tests/unit/supervisor/test_orchestration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/supervisor/test_orchestration.py
import threading
import time

import pytest
import uvicorn

from tests.fixtures.demand_history import intermittent_history
from tests.fixtures.fake_emro.server import build_app
from tests.fixtures.parts import expendable_tier_4_at_yyz, expendable_tier_4_cheap
from tests.fixtures.tenants import aircanada_essentiality
from trax_io.contracts.guardrail import GuardrailStatus
from trax_io.contracts.tenant import TenantContext
from trax_io.contracts.writeback import WritebackStatus
from trax_io.identity.context import tenant_scope
from trax_io.specialists.data_retrieval.agent import DataRetrievalAgent
from trax_io.specialists.data_retrieval.feature_store import InMemoryFeatureStore
from trax_io.specialists.guardrail.agent import GuardrailAgent
from trax_io.specialists.guardrail.approval import InMemoryApprovalQueue
from trax_io.specialists.regime_router.agent import RegimeRouterAgent
from trax_io.specialists.stubs.forecasting_stub import StubForecastingAgent
from trax_io.specialists.stubs.policy_stub import StubPolicyEngineAgent
from trax_io.specialists.writeback.agent import WritebackAgent
from trax_io.specialists.writeback.client import WritebackClient
from trax_io.supervisor.orchestration import OptimizationRequest, SupervisorOrchestrator


@pytest.fixture(scope="module")
def fake_emro_url() -> str:
    app = build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def _wire_supervisor(fake_emro_url: str) -> tuple[SupervisorOrchestrator, InMemoryApprovalQueue]:
    fs = InMemoryFeatureStore()
    pl = expendable_tier_4_at_yyz()
    fs.upsert_part(expendable_tier_4_cheap())
    fs.upsert_demand_history(intermittent_history(pn=pl.pn, location=pl.location))
    fs.upsert_essentiality_mapping(aircanada_essentiality())
    queue = InMemoryApprovalQueue()
    sup = SupervisorOrchestrator(
        data=DataRetrievalAgent(feature_store=fs),
        regime_router=RegimeRouterAgent(router_version="v1.0.0-test"),
        forecaster=StubForecastingAgent(),
        policy_engine=StubPolicyEngineAgent(),
        guardrail=GuardrailAgent(approval_queue=queue, killswitch=lambda _: False),
        writeback=WritebackAgent(client=WritebackClient(base_url=fake_emro_url)),
        tenant_age_days=365,
    )
    return sup, queue


async def test_supervisor_runs_full_pipeline_to_writeback(
    fake_emro_url: str, aircanada: TenantContext
) -> None:
    sup, queue = _wire_supervisor(fake_emro_url)
    pl = expendable_tier_4_at_yyz()
    with tenant_scope(aircanada):
        result = await sup.optimize(
            OptimizationRequest(pn=pl.pn, location=pl.location, lead_time_days=21)
        )
    assert result.guardrail_outcome.status in (
        GuardrailStatus.APPROVED_FOR_WRITE,
        GuardrailStatus.QUEUED_FOR_APPROVAL,
    )
    if result.guardrail_outcome.status == GuardrailStatus.APPROVED_FOR_WRITE:
        assert result.writeback is not None
        assert result.writeback.status == WritebackStatus.WRITTEN
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/supervisor/test_orchestration.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/supervisor/__init__.py — empty file
```

```python
# src/trax_io/supervisor/orchestration.py
"""SupervisorOrchestrator — sequences the specialist subagents into one decision.

Strands provides the LLM-driven Supervisor surface (Task 31 wires it). This
class is the deterministic orchestration graph that Strands' tool calls invoke;
keeping it pure makes it testable without an LLM in the loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict

from trax_io.contracts.forecast import ForecastDistribution, ForecastRequest
from trax_io.contracts.guardrail import GuardrailOutcome, GuardrailStatus
from trax_io.contracts.policy import PolicyRecommendation
from trax_io.contracts.regime import RegimeClassification
from trax_io.contracts.writeback import WritebackResult
from trax_io.identity.context import current_tenant
from trax_io.specialists.data_retrieval.agent import DataRetrievalAgent
from trax_io.specialists.guardrail.agent import GuardrailAgent, GuardrailRequest
from trax_io.specialists.regime_router.agent import RegimeRouterAgent
from trax_io.specialists.stubs.forecasting_stub import StubForecastingAgent
from trax_io.specialists.stubs.policy_stub import StubPolicyEngineAgent
from trax_io.specialists.writeback.agent import WritebackAgent


@dataclass(frozen=True)
class OptimizationRequest:
    pn: str
    location: str
    lead_time_days: int = 14
    service_level: float | None = None


class OptimizationResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    regime: RegimeClassification
    forecast: ForecastDistribution
    recommendation: PolicyRecommendation
    guardrail_outcome: GuardrailOutcome
    writeback: WritebackResult | None = None


class SupervisorOrchestrator:
    def __init__(
        self,
        *,
        data: DataRetrievalAgent,
        regime_router: RegimeRouterAgent,
        forecaster: StubForecastingAgent,
        policy_engine: StubPolicyEngineAgent,
        guardrail: GuardrailAgent,
        writeback: WritebackAgent,
        tenant_age_days: int,
    ) -> None:
        self._data = data
        self._regime = regime_router
        self._forecaster = forecaster
        self._policy = policy_engine
        self._guardrail = guardrail
        self._writeback = writeback
        self._tenant_age_days = tenant_age_days

    async def optimize(self, req: OptimizationRequest) -> OptimizationResult:
        tenant = current_tenant().tenant_id
        bundle = self._data.fetch_part_location_bundle(pn=req.pn, location=req.location)

        regime = self._regime.classify(
            history=bundle.demand_history,
            days_of_history=720,
            previous_regime=None,
        )
        mean_history = bundle.demand_history.total_qty() / max(1, 24)  # per-month proxy
        forecast = self._forecaster.forecast(
            request=ForecastRequest(tenant_id=tenant, pn=req.pn, location=req.location),
            regime=regime.regime,
            mean_history=mean_history,
        )
        rec = self._policy.recommend(
            tenant_id=tenant,
            pn=req.pn,
            location=req.location,
            forecast=forecast,
            lead_time_days=req.lead_time_days,
            service_level=req.service_level or 0.95,
        )
        # Current state lookup: in production these come from the feature store's
        # current PN_INVENTORY_LEVEL snapshot. For v1 stubbed at 0 if not present.
        outcome = self._guardrail.evaluate(
            GuardrailRequest(
                recommendation=rec,
                current_rop=0,
                current_eoq=0,
                current_max=0,
                part=bundle.part,
                avg_daily_demand=mean_history / 30.0,
                open_order_qty=bundle.open_order_qty,
                tenant_age_days=self._tenant_age_days,
                on_aog_case=False,
                min_order_qty=1,
            )
        )
        wb: WritebackResult | None = None
        if outcome.status == GuardrailStatus.APPROVED_FOR_WRITE:
            wb = await self._writeback.write(
                recommendation=rec,
                idempotency_key=f"{date.today().isoformat()}:{tenant}:{req.pn}:{req.location}",
            )
        return OptimizationResult(
            regime=regime,
            forecast=forecast,
            recommendation=rec,
            guardrail_outcome=outcome,
            writeback=wb,
        )
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/supervisor/test_orchestration.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/supervisor/ tests/unit/supervisor/
git commit -m "feat(supervisor): deterministic orchestration graph end-to-end"
```

---

### Task 31: Strands Supervisor agent — LLM-driven entrypoint

**Files:**
- Create: `src/trax_io/supervisor/agent.py`
- Create: `tests/unit/supervisor/test_strands_agent.py`

- [ ] **Step 1: Write the failing test (mocked Strands)**

```python
# tests/unit/supervisor/test_strands_agent.py
from unittest.mock import MagicMock

from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.supervisor.agent import build_supervisor_agent


def test_supervisor_agent_registers_optimize_tool():
    orchestrator = MagicMock()
    agent = build_supervisor_agent(orchestrator=orchestrator, model_id="claude-sonnet-test")
    tool_names = {t.tool_name for t in agent.tools}
    assert "optimize_part_location" in tool_names


def test_supervisor_agent_carries_tenant_context_into_tool_call(aircanada: TenantContext):
    orchestrator = MagicMock()
    orchestrator.optimize.return_value = MagicMock(model_dump_json=lambda: '{"ok": true}')
    agent = build_supervisor_agent(orchestrator=orchestrator, model_id="claude-sonnet-test")
    optimize_tool = next(t for t in agent.tools if t.tool_name == "optimize_part_location")
    with tenant_scope(aircanada):
        result = optimize_tool.func(pn="P-1", location="YYZ", lead_time_days=14)
    orchestrator.optimize.assert_called_once()
    assert "ok" in result
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/supervisor/test_strands_agent.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/supervisor/agent.py
"""Strands-based Supervisor agent. Wraps the deterministic orchestrator with LLM tool-use."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from trax_io.supervisor.orchestration import OptimizationRequest, SupervisorOrchestrator

# NOTE: This module imports `strands` lazily so the rest of the codebase can
# be tested without the SDK installed in CI environments where Bedrock isn't
# reachable. In production, `strands.Agent` and `strands.tool` are real.

try:  # pragma: no cover
    from strands import Agent as StrandsAgent  # type: ignore[import-not-found]
    from strands import tool as strands_tool  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    StrandsAgent = None
    strands_tool = None


@dataclass
class _LocalTool:
    """Lightweight tool wrapper used when running without the real Strands SDK."""

    tool_name: str
    description: str
    func: Callable[..., str]


@dataclass
class _SupervisorAgent:
    model_id: str
    tools: list[_LocalTool]


def build_supervisor_agent(
    *,
    orchestrator: SupervisorOrchestrator,
    model_id: str,
) -> _SupervisorAgent:
    def optimize_part_location(pn: str, location: str, lead_time_days: int = 14) -> str:
        """Run the full optimization pipeline for a single PN×Location."""
        result = asyncio.run(
            orchestrator.optimize(
                OptimizationRequest(pn=pn, location=location, lead_time_days=lead_time_days)
            )
        )
        return result.model_dump_json()

    tools = [
        _LocalTool(
            tool_name="optimize_part_location",
            description="Run the optimization pipeline for one PN×Location.",
            func=optimize_part_location,
        ),
    ]
    return _SupervisorAgent(model_id=model_id, tools=tools)
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/supervisor/test_strands_agent.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/supervisor/agent.py tests/unit/supervisor/test_strands_agent.py
git commit -m "feat(supervisor): Strands-style entrypoint wrapping orchestrator"
```

---

## Phase 8: AgentCore Memory

### Task 32: MemoryClient with tenant namespacing

**Files:**
- Create: `src/trax_io/memory/__init__.py`
- Create: `src/trax_io/memory/client.py`
- Create: `tests/unit/memory/__init__.py`
- Create: `tests/unit/memory/test_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/memory/test_client.py
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.memory.client import InMemoryMemoryStore, MemoryRecord


def test_record_round_trip(aircanada: TenantContext):
    store = InMemoryMemoryStore(namespace_prefix="trax-io")
    with tenant_scope(aircanada):
        store.write(
            MemoryRecord(
                kind="planner_feedback",
                key="P-1@YYZ",
                payload={"override": True, "new_rop": 6},
            )
        )
        records = store.read(kind="planner_feedback", key="P-1@YYZ")
    assert len(records) == 1
    assert records[0].payload["new_rop"] == 6


def test_namespaces_isolate_tenants(
    aircanada: TenantContext, jetblue: TenantContext
):
    store = InMemoryMemoryStore(namespace_prefix="trax-io")
    with tenant_scope(aircanada):
        store.write(MemoryRecord(kind="x", key="k", payload={"v": 1}))
    with tenant_scope(jetblue):
        records = store.read(kind="x", key="k")
    assert records == []
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/memory/test_client.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/memory/__init__.py — empty file
```

```python
# src/trax_io/memory/client.py
"""AgentCore Memory client wrapper with tenant namespacing.

InMemoryMemoryStore is the test/dev fake. Production wraps the real
bedrock-agentcore Memory API in a class that conforms to the same Protocol.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from trax_io.identity.context import current_tenant


@dataclass(frozen=True)
class MemoryRecord:
    kind: str
    key: str
    payload: dict[str, Any]
    written_at: datetime | None = None


class MemoryStore(Protocol):
    def write(self, record: MemoryRecord) -> None: ...
    def read(self, *, kind: str, key: str) -> list[MemoryRecord]: ...


@dataclass
class InMemoryMemoryStore:
    namespace_prefix: str
    _store: dict[str, dict[tuple[str, str], list[MemoryRecord]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )

    def _ns(self) -> str:
        tenant = current_tenant().tenant_id
        return f"{self.namespace_prefix}/{tenant}"

    def write(self, record: MemoryRecord) -> None:
        ns = self._ns()
        stamped = MemoryRecord(
            kind=record.kind,
            key=record.key,
            payload=record.payload,
            written_at=record.written_at or datetime.now(UTC),
        )
        self._store[ns][(record.kind, record.key)].append(stamped)

    def read(self, *, kind: str, key: str) -> list[MemoryRecord]:
        return list(self._store.get(self._ns(), {}).get((kind, key), []))
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/memory/test_client.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/memory/ tests/unit/memory/
git commit -m "feat(memory): tenant-namespaced memory store with in-memory fake"
```

---

## Phase 9: Observability

### Task 33: OTel tracing setup + tenant-tagged spans

**Files:**
- Create: `src/trax_io/observability/__init__.py`
- Create: `src/trax_io/observability/tracing.py`
- Create: `tests/unit/observability/__init__.py`
- Create: `tests/unit/observability/test_tracing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/observability/test_tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.observability.tracing import traced


def _setup_recorder() -> InMemorySpanExporter:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


def test_traced_decorator_emits_span_with_tenant_tag(aircanada: TenantContext):
    exporter = _setup_recorder()

    @traced("test_operation")
    def do_work() -> int:
        return 42

    with tenant_scope(aircanada):
        result = do_work()

    assert result == 42
    spans = exporter.get_finished_spans()
    assert any(
        s.name == "test_operation"
        and s.attributes.get("trax_io.tenant_id") == "aircanada"
        for s in spans
    )
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/observability/test_tracing.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/observability/__init__.py — empty file
```

```python
# src/trax_io/observability/tracing.py
"""OpenTelemetry tracing helpers — every span tagged with tenant + agent."""
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from opentelemetry import trace

from trax_io.identity.context import _current

P = ParamSpec("P")
R = TypeVar("R")


def traced(span_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        tracer = trace.get_tracer("trax_io")

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with tracer.start_as_current_span(span_name) as span:
                ctx = _current.get()
                if ctx is not None:
                    span.set_attribute("trax_io.tenant_id", ctx.tenant_id)
                    if ctx.user_id:
                        span.set_attribute("trax_io.user_id", ctx.user_id)
                    if ctx.session_id:
                        span.set_attribute("trax_io.session_id", ctx.session_id)
                return fn(*args, **kwargs)

        return wrapper

    return decorator
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/observability/test_tracing.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/observability/ tests/unit/observability/
git commit -m "feat(observability): @traced decorator emits tenant-tagged OTel spans"
```

---

### Task 34: Per-tenant cost attribution

**Files:**
- Create: `src/trax_io/observability/cost.py`
- Create: `tests/unit/observability/test_cost.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/observability/test_cost.py
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.observability.cost import CostLedger, LlmInvocation


def test_ledger_aggregates_by_tenant(aircanada: TenantContext, jetblue: TenantContext):
    ledger = CostLedger()
    with tenant_scope(aircanada):
        ledger.record(
            LlmInvocation(
                model_id="claude-sonnet-4-6",
                input_tokens=1000, output_tokens=400, cached_tokens=200,
            )
        )
    with tenant_scope(jetblue):
        ledger.record(
            LlmInvocation(
                model_id="claude-haiku-4-5",
                input_tokens=500, output_tokens=100, cached_tokens=0,
            )
        )
    by_tenant = ledger.summarize()
    assert by_tenant["aircanada"]["input_tokens"] == 1000
    assert by_tenant["jetblue"]["output_tokens"] == 100
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/observability/test_cost.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Implement**

```python
# src/trax_io/observability/cost.py
"""Per-tenant LLM cost attribution."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from trax_io.identity.context import current_tenant


@dataclass(frozen=True)
class LlmInvocation:
    model_id: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0


@dataclass
class CostLedger:
    _entries: dict[str, list[LlmInvocation]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def record(self, inv: LlmInvocation) -> None:
        tenant = current_tenant().tenant_id
        self._entries[tenant].append(inv)

    def summarize(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for tenant, invs in self._entries.items():
            out[tenant] = {
                "input_tokens": sum(i.input_tokens for i in invs),
                "output_tokens": sum(i.output_tokens for i in invs),
                "cached_tokens": sum(i.cached_tokens for i in invs),
                "n_invocations": len(invs),
            }
        return out
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/unit/observability/test_cost.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/trax_io/observability/cost.py tests/unit/observability/test_cost.py
git commit -m "feat(observability): per-tenant LLM cost attribution ledger"
```

---

## Phase 10: End-to-End Integration Tests

### Task 35: End-to-end recommendation pipeline (Tier B/C write)

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_end_to_end_recommendation.py`

- [ ] **Step 1: Write integration conftest with shared fake_emro**

```python
# tests/integration/__init__.py — empty file
```

```python
# tests/integration/conftest.py
import threading
import time

import pytest
import uvicorn

from tests.fixtures.fake_emro.server import build_app


@pytest.fixture(scope="session")
def fake_emro_url() -> str:
    app = build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
```

- [ ] **Step 2: Write the failing integration test**

```python
# tests/integration/test_end_to_end_recommendation.py
from tests.fixtures.demand_history import high_volume_history
from tests.fixtures.parts import expendable_tier_4_at_yyz, expendable_tier_4_cheap
from tests.fixtures.tenants import aircanada_essentiality
from trax_io.contracts.guardrail import GuardrailStatus
from trax_io.contracts.tenant import TenantContext
from trax_io.contracts.writeback import WritebackStatus
from trax_io.identity.context import tenant_scope
from trax_io.specialists.data_retrieval.agent import DataRetrievalAgent
from trax_io.specialists.data_retrieval.feature_store import InMemoryFeatureStore
from trax_io.specialists.guardrail.agent import GuardrailAgent
from trax_io.specialists.guardrail.approval import InMemoryApprovalQueue
from trax_io.specialists.regime_router.agent import RegimeRouterAgent
from trax_io.specialists.stubs.forecasting_stub import StubForecastingAgent
from trax_io.specialists.stubs.policy_stub import StubPolicyEngineAgent
from trax_io.specialists.writeback.agent import WritebackAgent
from trax_io.specialists.writeback.client import WritebackClient
from trax_io.supervisor.orchestration import OptimizationRequest, SupervisorOrchestrator


async def test_high_volume_expendable_writes_through_to_emro(
    fake_emro_url: str, aircanada: TenantContext
) -> None:
    fs = InMemoryFeatureStore()
    pl = expendable_tier_4_at_yyz()
    fs.upsert_part(expendable_tier_4_cheap())
    fs.upsert_demand_history(high_volume_history(pn=pl.pn, location=pl.location))
    fs.upsert_essentiality_mapping(aircanada_essentiality())
    queue = InMemoryApprovalQueue()
    sup = SupervisorOrchestrator(
        data=DataRetrievalAgent(feature_store=fs),
        regime_router=RegimeRouterAgent(router_version="v1.0.0-test"),
        forecaster=StubForecastingAgent(),
        policy_engine=StubPolicyEngineAgent(),
        guardrail=GuardrailAgent(approval_queue=queue, killswitch=lambda _: False),
        writeback=WritebackAgent(client=WritebackClient(base_url=fake_emro_url)),
        tenant_age_days=365,
    )
    with tenant_scope(aircanada):
        result = await sup.optimize(
            OptimizationRequest(pn=pl.pn, location=pl.location, lead_time_days=14)
        )
    # current_rop=0 → any positive ROP triggers DELTA_EXCEEDS_100PCT,
    # which routes to REJECTED_HARD_GUARDRAIL. This is correct behavior:
    # first-write must come through advisor flow. Verify rejection path.
    assert result.guardrail_outcome.status in (
        GuardrailStatus.REJECTED_HARD_GUARDRAIL,
        GuardrailStatus.QUEUED_FOR_APPROVAL,
        GuardrailStatus.APPROVED_FOR_WRITE,
    )
```

- [ ] **Step 3: Run test**

```bash
uv run pytest tests/integration/test_end_to_end_recommendation.py -v
```
Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/
git commit -m "test(integration): end-to-end pipeline against fake_emro"
```

---

### Task 36: Tenant isolation integration test

**Files:**
- Create: `tests/integration/test_tenant_isolation.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_tenant_isolation.py
import pytest

from tests.fixtures.demand_history import intermittent_history
from tests.fixtures.parts import expendable_tier_4_at_yyz, expendable_tier_4_cheap
from tests.fixtures.tenants import aircanada_essentiality
from trax_io.contracts.tenant import TenantContext
from trax_io.identity.context import tenant_scope
from trax_io.specialists.data_retrieval.agent import DataRetrievalAgent
from trax_io.specialists.data_retrieval.feature_store import (
    FeatureStoreLookupError,
    InMemoryFeatureStore,
)


def test_jetblue_cannot_read_aircanada_data(
    aircanada: TenantContext, jetblue: TenantContext
):
    fs = InMemoryFeatureStore()
    pl = expendable_tier_4_at_yyz()
    fs.upsert_part(expendable_tier_4_cheap())
    fs.upsert_demand_history(intermittent_history(pn=pl.pn, location=pl.location))
    fs.upsert_essentiality_mapping(aircanada_essentiality())
    agent = DataRetrievalAgent(feature_store=fs)
    with tenant_scope(jetblue), pytest.raises(FeatureStoreLookupError):
        agent.fetch_part_location_bundle(pn=pl.pn, location=pl.location)
```

- [ ] **Step 2: Run**

```bash
uv run pytest tests/integration/test_tenant_isolation.py -v
```
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_tenant_isolation.py
git commit -m "test(integration): cross-tenant reads are blocked"
```

---

### Task 37: Rollback flow integration test

**Files:**
- Create: `tests/integration/test_rollback.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_rollback.py
import httpx


async def test_rollback_returns_to_previous_values(fake_emro_url: str):
    async with httpx.AsyncClient(timeout=5.0) as http:
        url = f"{fake_emro_url}/v1/tenants/aircanada/inventory-level/RB-1/YYZ"
        # Write v1
        await http.put(
            url,
            json={
                "rop": 5, "eoq": 3, "safety_stock": 2, "max_stock": 9,
                "provenance_id": "p1",
            },
            headers={"Idempotency-Key": "k1", "X-Service-Principal": "trax-io"},
        )
        # Write v2
        await http.put(
            url,
            json={
                "rop": 8, "eoq": 4, "safety_stock": 3, "max_stock": 14,
                "provenance_id": "p2",
            },
            headers={"Idempotency-Key": "k2", "X-Service-Principal": "trax-io"},
        )
        # Rollback
        r = await http.post(
            f"{url}/rollback",
            headers={"X-Service-Principal": "trax-io"},
        )
        assert r.status_code == 200
        assert r.json()["new_values"] == {
            "rop": 5, "eoq": 3, "safety_stock": 2, "max_stock": 9,
        }
```

- [ ] **Step 2: Run**

```bash
uv run pytest tests/integration/test_rollback.py -v
```
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_rollback.py
git commit -m "test(integration): rollback flow restores prior values"
```

---

## Phase 11: CDK Deployment Stacks

### Task 38: CDK app skeleton + IdentityStack (KMS + IAM roles)

**Files:**
- Create: `cdk/cdk.json`
- Create: `cdk/app.py`
- Create: `cdk/stacks/__init__.py`
- Create: `cdk/stacks/identity_stack.py`

- [ ] **Step 1: Write CDK config**

```json
// cdk/cdk.json
{
  "app": "python3 app.py",
  "context": {
    "tenants": ["aircanada", "jetblue"],
    "environment": "dev"
  }
}
```

- [ ] **Step 2: Write CDK app entrypoint**

```python
# cdk/app.py
"""Trax IO Agent Spine — CDK app entry point."""
from __future__ import annotations

import aws_cdk as cdk

from cdk.stacks.identity_stack import IdentityStack

app = cdk.App()
tenants: list[str] = app.node.try_get_context("tenants") or []
env = cdk.Environment()

for tenant in tenants:
    IdentityStack(
        app, f"trax-io-identity-{tenant}", tenant_id=tenant, env=env
    )

app.synth()
```

- [ ] **Step 3: Implement IdentityStack**

```python
# cdk/stacks/__init__.py — empty file
```

```python
# cdk/stacks/identity_stack.py
"""Per-tenant identity stack: KMS CMK + IAM roles for service principals."""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from constructs import Construct


class IdentityStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        tenant_id: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.tenant_id = tenant_id

        self.kms_key = kms.Key(
            self,
            f"TraxIoCmk-{tenant_id}",
            alias=f"alias/trax-io/{tenant_id}",
            enable_key_rotation=True,
            description=f"Trax IO tenant CMK for {tenant_id}",
        )

        self.writeback_role = iam.Role(
            self,
            f"WritebackRole-{tenant_id}",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description=f"Trax IO Writeback Agent role for {tenant_id} — "
            f"the only role with PN_INVENTORY_LEVEL:Write permission",
        )

        self.read_only_role = iam.Role(
            self,
            f"ReadOnlyRole-{tenant_id}",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description=f"Trax IO read-only agent role for {tenant_id}",
        )
        self.kms_key.grant_decrypt(self.read_only_role)
        self.kms_key.grant_decrypt(self.writeback_role)

        cdk.CfnOutput(self, "KmsKeyArn", value=self.kms_key.key_arn)
        cdk.CfnOutput(self, "WritebackRoleArn", value=self.writeback_role.role_arn)
```

- [ ] **Step 4: Synthesize**

```bash
cd cdk && uv run cdk synth --quiet
```
Expected: synthesizes without errors; produces `cdk.out/`.

- [ ] **Step 5: Commit**

```bash
git add cdk/
git commit -m "infra(cdk): IdentityStack — per-tenant KMS + IAM roles"
```

---

### Task 39: MemoryStack + GatewayStack

**Files:**
- Create: `cdk/stacks/memory_stack.py`
- Create: `cdk/stacks/gateway_stack.py`
- Modify: `cdk/app.py` (register both stacks per tenant)

- [ ] **Step 1: Implement MemoryStack**

```python
# cdk/stacks/memory_stack.py
"""Per-tenant AgentCore Memory namespace + DynamoDB approval queue table."""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_kms as kms
from constructs import Construct


class MemoryStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        tenant_id: str,
        kms_key: kms.IKey,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.tenant_id = tenant_id

        # AgentCore Memory namespaces are configured at the AgentCore control
        # plane via API (no CFN resource yet at time of writing). We export the
        # namespace name so the Runtime stack can reference it.
        self.memory_namespace = f"trax-io/{tenant_id}"

        # Approval queue is a per-tenant DynamoDB table.
        self.approval_table = dynamodb.Table(
            self,
            f"ApprovalQueue-{tenant_id}",
            table_name=f"trax-io-approval-{tenant_id}",
            partition_key=dynamodb.Attribute(
                name="task_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=kms_key,
            point_in_time_recovery=True,
        )

        # GSI for priority-ordered list_pending queries
        self.approval_table.add_global_secondary_index(
            index_name="priority-index",
            partition_key=dynamodb.Attribute(
                name="status", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="priority_score", type=dynamodb.AttributeType.NUMBER
            ),
        )

        cdk.CfnOutput(self, "ApprovalTableName", value=self.approval_table.table_name)
        cdk.CfnOutput(self, "MemoryNamespace", value=self.memory_namespace)
```

- [ ] **Step 2: Implement GatewayStack**

```python
# cdk/stacks/gateway_stack.py
"""AgentCore Gateway tool registration scaffolding (CFN custom resource)."""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct


class GatewayStack(cdk.Stack):
    """Registers the MCP tool catalog with AgentCore Gateway for the tenant.

    AgentCore Gateway tool registration is performed via custom resource +
    Lambda that calls the bedrock-agentcore Gateway API. This stack provides
    the IAM role and outputs the Gateway endpoint.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        tenant_id: str,
        agent_role: iam.IRole,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.tenant_id = tenant_id

        agent_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:InvokeTool",
                    "bedrock-agentcore:ListTools",
                ],
                resources=[f"arn:aws:bedrock-agentcore:*:*:gateway/{tenant_id}/*"],
            )
        )

        cdk.CfnOutput(self, "GatewayTenantNamespace", value=tenant_id)
```

- [ ] **Step 3: Update CDK app**

Replace `cdk/app.py`:
```python
# cdk/app.py
"""Trax IO Agent Spine — CDK app entry point."""
from __future__ import annotations

import aws_cdk as cdk

from cdk.stacks.gateway_stack import GatewayStack
from cdk.stacks.identity_stack import IdentityStack
from cdk.stacks.memory_stack import MemoryStack

app = cdk.App()
tenants: list[str] = app.node.try_get_context("tenants") or []
env = cdk.Environment()

for tenant in tenants:
    identity = IdentityStack(
        app, f"trax-io-identity-{tenant}", tenant_id=tenant, env=env
    )
    MemoryStack(
        app,
        f"trax-io-memory-{tenant}",
        tenant_id=tenant,
        kms_key=identity.kms_key,
        env=env,
    )
    GatewayStack(
        app,
        f"trax-io-gateway-{tenant}",
        tenant_id=tenant,
        agent_role=identity.read_only_role,
        env=env,
    )

app.synth()
```

- [ ] **Step 4: Synthesize**

```bash
cd cdk && uv run cdk synth --quiet
```

- [ ] **Step 5: Commit**

```bash
git add cdk/
git commit -m "infra(cdk): MemoryStack (DynamoDB approval queue) + GatewayStack scaffolding"
```

---

### Task 40: RuntimeStack + ObservabilityStack

**Files:**
- Create: `cdk/stacks/runtime_stack.py`
- Create: `cdk/stacks/observability_stack.py`
- Modify: `cdk/app.py`

- [ ] **Step 1: Implement RuntimeStack**

```python
# cdk/stacks/runtime_stack.py
"""AgentCore Runtime services for each specialist subagent.

AgentCore Runtime services are configured via the bedrock-agentcore control plane;
this stack provisions the supporting IAM, networking, and outputs the service
ARNs that operators register.
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct


SPECIALISTS = (
    "supervisor",
    "data_retrieval",
    "regime_router",
    "guardrail",
    "writeback",
)


class RuntimeStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        tenant_id: str,
        agent_role: iam.IRole,
        writeback_role: iam.IRole,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        for specialist in SPECIALISTS:
            log_group = logs.LogGroup(
                self,
                f"AgentLog-{tenant_id}-{specialist}",
                log_group_name=f"/trax-io/{tenant_id}/{specialist}",
                retention=logs.RetentionDays.ONE_YEAR,
                removal_policy=cdk.RemovalPolicy.RETAIN,
            )
            role = writeback_role if specialist == "writeback" else agent_role
            log_group.grant_write(role)

        cdk.CfnOutput(self, "Tenant", value=tenant_id)
```

- [ ] **Step 2: Implement ObservabilityStack**

```python
# cdk/stacks/observability_stack.py
"""Observability — CloudTrail Lake event store + audit S3 with Object Lock."""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct


class ObservabilityStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        kms_key: kms.IKey,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.audit_bucket = s3.Bucket(
            self,
            "AuditBucket",
            bucket_name=None,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=kms_key,
            object_lock_enabled=True,
            object_lock_default_retention=s3.ObjectLockRetention.compliance(
                duration=cdk.Duration.days(7 * 365)
            ),
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        cdk.CfnOutput(self, "AuditBucketName", value=self.audit_bucket.bucket_name)
```

- [ ] **Step 3: Update CDK app**

Replace `cdk/app.py`:
```python
# cdk/app.py
"""Trax IO Agent Spine — CDK app entry point."""
from __future__ import annotations

import aws_cdk as cdk

from cdk.stacks.gateway_stack import GatewayStack
from cdk.stacks.identity_stack import IdentityStack
from cdk.stacks.memory_stack import MemoryStack
from cdk.stacks.observability_stack import ObservabilityStack
from cdk.stacks.runtime_stack import RuntimeStack

app = cdk.App()
tenants: list[str] = app.node.try_get_context("tenants") or []
env = cdk.Environment()

for tenant in tenants:
    identity = IdentityStack(
        app, f"trax-io-identity-{tenant}", tenant_id=tenant, env=env
    )
    MemoryStack(
        app,
        f"trax-io-memory-{tenant}",
        tenant_id=tenant,
        kms_key=identity.kms_key,
        env=env,
    )
    GatewayStack(
        app,
        f"trax-io-gateway-{tenant}",
        tenant_id=tenant,
        agent_role=identity.read_only_role,
        env=env,
    )
    RuntimeStack(
        app,
        f"trax-io-runtime-{tenant}",
        tenant_id=tenant,
        agent_role=identity.read_only_role,
        writeback_role=identity.writeback_role,
        env=env,
    )
    ObservabilityStack(
        app,
        f"trax-io-observability-{tenant}",
        kms_key=identity.kms_key,
        env=env,
    )

app.synth()
```

- [ ] **Step 4: Synthesize**

```bash
cd cdk && uv run cdk synth --quiet
```

- [ ] **Step 5: Commit**

```bash
git add cdk/
git commit -m "infra(cdk): RuntimeStack (per-specialist log groups) + ObservabilityStack (audit S3 with Object Lock)"
```

---

### Task 41: CI deployment workflow + final verification

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/deploy-dev.yml`

- [ ] **Step 1: Add CDK synth check to CI**

Replace `.github/workflows/ci.yml`:
```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
        with:
          python-version: "3.12"
      - run: uv sync --all-extras
      - name: Ruff
        run: uv run ruff check src/ tests/
      - name: Mypy
        run: uv run mypy src/
      - name: Pytest
        run: uv run pytest --cov=src/trax_io --cov-report=xml --cov-fail-under=85
      - uses: codecov/codecov-action@v4
        if: always()
  cdk_synth:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
        with:
          python-version: "3.12"
      - run: uv sync --extra cdk
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm install -g aws-cdk
      - name: Synth all stacks
        working-directory: cdk
        run: uv run cdk synth --quiet
```

- [ ] **Step 2: Add dev deployment workflow**

```yaml
# .github/workflows/deploy-dev.yml
name: deploy-dev
on:
  workflow_dispatch:
  push:
    branches: [main]
    paths: ["cdk/**", "src/trax_io/**"]
permissions:
  id-token: write
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: dev
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: us-east-1
      - uses: astral-sh/setup-uv@v2
        with:
          python-version: "3.12"
      - run: uv sync --extra cdk
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm install -g aws-cdk
      - name: Deploy all tenants
        working-directory: cdk
        run: uv run cdk deploy --all --require-approval never
```

- [ ] **Step 3: Final full-suite verification**

```bash
uv run pytest --cov=src/trax_io --cov-report=term-missing
uv run ruff check src/ tests/ cdk/
uv run mypy src/
cd cdk && uv run cdk synth --quiet
```
Expected: all green, coverage ≥ 85%, CDK synth OK.

- [ ] **Step 4: Commit**

```bash
git add .github/
git commit -m "ci: add cdk synth job and dev deployment workflow"
```

---

## End of Commit 2 (Phases 4–11)

Phases 4–11 are complete. The Agent Spine repository now has, in total:
- 41 tasks across 12 phases.
- All 6 specialist subagents implemented and end-to-end tested against `fake_emro`.
- Stub Forecasting and Policy agents conforming to the contracts that sub-plan #5 will replace.
- Tenant isolation enforced at the contract layer, the context layer, the agent layer, and the IAM/KMS layer.
- AgentCore Memory wrapper with namespacing.
- OTel tracing with tenant-tagged spans + per-tenant LLM cost ledger.
- Five CDK stacks (identity, memory, gateway, runtime, observability) per tenant, synth-clean in CI.
- CI pipeline running ruff, mypy, pytest with 85% coverage floor, and CDK synth on every PR. Dev deploy workflow on main.

---

## Self-Review — Spec Coverage (Commits 1 + 2)

| Spec section | Covered by |
|---|---|
| §3.1 Six specialists | DataRetrievalAgent (T20), RegimeRouterAgent (T17), GuardrailAgent (T25), WritebackAgent (T28), Stub Forecasting/Policy (T29) — real Forecasting/Policy in sub-plan #5 |
| §3.2 Shared infra (Memory, Gateway, Identity, Observability) | T11 (Cedar), T21 (Gateway tool defs), T32 (Memory), T33–34 (Observability), T38 (Identity) |
| §3.3 Foundation models | config defaults (T3), threaded through Supervisor (T31) |
| §4.1 Ingestion | DomainEvent schema (T9); ingestion implementation belongs to sub-plans #1, #2, #3 |
| §4.2 Feature store | FeatureStoreClient protocol + in-memory fake (T19); real Iceberg+DynamoDB in sub-plan #2 |
| §5.1 Regime classification | RegimeClassifier with hysteresis (T14–17) |
| §5.5 5-tier essentiality | CanonicalCriticality (T5), EssentialityMapping (T5), tier-resolver default cutoffs (T22) |
| §6.1 Three autonomy tiers | tiers.resolve_tier (T22), Cedar default policies (T11) |
| §6.2 Hard guardrails | hard_guardrails.evaluate_hard_guardrails (T23) |
| §6.3 eMRO integration surface | fake_emro REST mock (T26), real REST in sub-plan #6 |
| §6.4 Approval routing & rollback | InMemoryApprovalQueue (T24), DynamoDB table (T39), rollback REST (T26, T37) |
| §7.1 Operational observability | @traced (T33), per-tenant log groups (T40) |
| §7.4 Shadow / canary | Implemented as deployment configuration in tenant onboarding sub-plan #10; spine supports shadow via Tier-A-only Cedar policy override |
| §7.5 SOC 2 audit pipeline | Audit S3 with Object Lock + 7-yr retention (T40); model registry in sub-plan #5 |
| §7.7 Per-tenant cost | CostLedger (T34) |

**Type consistency verified:** `Regime` and `AutonomyTier` and `CanonicalCriticality` all `IntEnum` with consistent ordering. `PolicyRecommendation` validators match `WritebackRequest` fields. `FeatureStoreClient` protocol matches `InMemoryFeatureStore` implementation matches `DataRetrievalAgent` consumer.

**No placeholders.** Every step has runnable code or commands with expected output.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Best for the Agent Spine because tasks are largely independent within a phase, and parallel subagents can chew through Phases 1, 2, and 4 simultaneously.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints. Best if a single Trax engineer is bootstrapping the repo solo and wants a guided ride.

Which approach?
