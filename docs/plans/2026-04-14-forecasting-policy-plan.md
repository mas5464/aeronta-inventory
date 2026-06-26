# Sub-plan #5 — Forecasting & Policy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the regime-routed ensemble forecasting stack and the deterministic policy engine that replace the stub specialists in sub-plan #4 Agent Spine. Ship four forecasting champions (compound-Poisson + empirical Bayes for `ultra_rare`, Croston/TSB/SBA for `intermittent`, LightGBM with causal covariates for `moderate` and `high_volume`) and a foundation-model challenger (Chronos). Add the champion/challenger evaluation pipeline, nightly model scoring, auto-promotion gating, and the cost-weighted MAPE scoreboard. Ship the deterministic `PolicyEngine` that converts forecast distributions + lead-time distributions + service-level targets + hard constraints into `(ROP, EOQ, SS, Max)` with full provenance.

**Architecture:** Two Strands specialists — `ForecastingAgent` and `PolicyEngineAgent` — both registered on AgentCore Runtime, both conforming to the interfaces defined in `src/trax_io/contracts/forecast.py` and `src/trax_io/contracts/policy.py` (already shipped by sub-plan #4). Forecasting dispatches to regime-specific model classes behind a uniform `ForecastingModel` Protocol; SageMaker hosts LightGBM and Chronos; classical stats run in-process. Policy Engine is pure Python — no LLM, no ML — and implements `(s,S)`, `(R,Q)`, and base-stock policies with interchangeability rollup, shelf-life clamps, and service-level-constrained safety stock math.

**Tech Stack:**
- Python 3.12
- `statsforecast` 1.7.x (Croston, TSB, SBA, IMAPA)
- `lightgbm` 4.x
- `chronos-forecasting` 1.x (Amazon's foundation-model forecaster)
- `scipy.stats` for compound-Poisson, NBD, gamma lead-time distributions
- `pydantic` v2 for all contract-conforming models
- AWS SageMaker (training + real-time endpoints for LightGBM and Chronos)
- SageMaker Model Registry for champion/challenger lineage
- `pyiceberg` to read features from the sub-plan #2 lake
- `pytest` + property-based tests via `hypothesis`
- Repository: `trax-io-forecasting` (separate repo from Agent Spine; published as `trax_io_forecasting` Python package imported by Spine)

**Dependencies:**
- **#2 Feature Store** — reads `demand_history`, `causal_utilization`, `lead_time_distribution`, `wash_rate_history`, `interchange_graph`, `part_attributes` Iceberg tables.
- **#4 Agent Spine** — imports this package; Forecasting + Policy specialists are loaded into the Supervisor's orchestration graph.
- **#9 Observability & SOC 2** — consumes model-registry lineage and evaluation metrics.

---

## File Structure

```
trax-io-forecasting/
├── pyproject.toml
├── README.md
├── src/trax_io_forecasting/
│   ├── __init__.py
│   ├── contracts/                # Re-exports from trax_io.contracts for convenience
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py               # ForecastingModel Protocol
│   │   ├── compound_poisson.py   # ultra_rare champion
│   │   ├── classical.py          # Croston / TSB / SBA / IMAPA via statsforecast
│   │   ├── lightgbm_model.py     # moderate + high_volume champion
│   │   ├── chronos_model.py      # foundation-model challenger
│   │   ├── ensemble.py           # weighted-MAPE ensemble for high_volume
│   │   └── registry.py           # champion / challenger registry client
│   ├── features/
│   │   ├── __init__.py
│   │   ├── loader.py             # Iceberg → model-ready feature vectors
│   │   ├── causal.py             # Causal covariates (flight hours, cycles, ATA, fleet)
│   │   └── priors.py             # Empirical-Bayes peer priors
│   ├── forecasting_agent.py      # Strands specialist — replaces stub
│   ├── policy/
│   │   ├── __init__.py
│   │   ├── engine.py             # PolicyEngine (deterministic, non-LLM)
│   │   ├── base_stock.py         # (S-1, S) for ultra_rare critical
│   │   ├── s_S.py                # (s, S) continuous review
│   │   ├── R_Q.py                # (R, Q) periodic review
│   │   ├── service_level.py      # Normal, NBD, and compound-Poisson service-level math
│   │   ├── lead_time.py          # Lead-time-demand distribution convolution
│   │   ├── interchange.py        # Group rollup + proportional apportionment
│   │   ├── constraints.py        # Shelf-life, hazmat, tool hard caps
│   │   └── provenance.py         # ProvenanceRecord serializer
│   ├── policy_agent.py           # Strands specialist — replaces stub
│   ├── evaluation/
│   │   ├── __init__.py
│   │   ├── metrics.py            # Weighted MAPE, fill-rate realization, total cost
│   │   ├── holdout.py            # 60-day rolling holdout splitter
│   │   ├── pipeline.py           # Nightly scoring Glue job
│   │   ├── champion_challenger.py # Auto-promotion gate (45-day dominance)
│   │   └── reports.py            # Per-tenant, per-regime scoreboard
│   ├── training/
│   │   ├── __init__.py
│   │   ├── lightgbm_train.py     # SageMaker training job entrypoint
│   │   ├── chronos_finetune.py   # Chronos fine-tune entrypoint
│   │   └── federated.py          # De-identified cross-tenant feature prep
│   └── sagemaker_endpoints/
│       ├── lightgbm_container/
│       └── chronos_container/
├── tests/
│   ├── unit/
│   │   ├── models/
│   │   │   ├── test_compound_poisson.py
│   │   │   ├── test_classical.py
│   │   │   ├── test_lightgbm.py
│   │   │   ├── test_chronos.py
│   │   │   └── test_ensemble.py
│   │   ├── policy/
│   │   │   ├── test_service_level.py
│   │   │   ├── test_base_stock.py
│   │   │   ├── test_s_S.py
│   │   │   ├── test_R_Q.py
│   │   │   ├── test_interchange.py
│   │   │   ├── test_constraints.py
│   │   │   └── test_engine.py
│   │   ├── evaluation/
│   │   │   ├── test_metrics.py
│   │   │   ├── test_holdout.py
│   │   │   └── test_champion_challenger.py
│   │   └── test_agents.py
│   ├── property/
│   │   └── test_policy_invariants.py
│   ├── integration/
│   │   ├── conftest.py
│   │   ├── test_end_to_end_forecast.py
│   │   └── test_agent_spine_integration.py
│   └── fixtures/
│       ├── demand_cases.py       # 30+ canonical demand patterns
│       └── lead_time_cases.py
├── infra/
│   └── sagemaker_stacks/         # CDK for endpoints, training jobs, model registry
└── docs/
    ├── ARCHITECTURE.md
    ├── MODEL_CARDS.md             # Per-model documentation for SOC 2 / AI-Act
    └── adr/
        ├── 0001-croston-vs-tsb-vs-sba.md
        ├── 0002-chronos-vs-moirai-challenger.md
        └── 0003-policy-engine-non-llm.md
```

---

## Phase Plan

| Phase | Scope | Tasks |
|---|---|---|
| 0 | Bootstrap + contract re-exports + test fixtures | 1–4 |
| 1 | Service-level math primitives | 5–8 |
| 2 | Classical intermittent forecasters (Croston, TSB, SBA) | 9–12 |
| 3 | Compound-Poisson + empirical-Bayes priors for `ultra_rare` | 13–16 |
| 4 | LightGBM with causal covariates | 17–21 |
| 5 | Chronos foundation-model challenger | 22–24 |
| 6 | Ensemble + model registry | 25–27 |
| 7 | Policy Engine: base-stock, (s,S), (R,Q) | 28–33 |
| 8 | Interchangeability rollup + constraints | 34–36 |
| 9 | Forecasting + Policy Strands specialists | 37–40 |
| 10 | Evaluation pipeline + champion/challenger promotion | 41–45 |
| 11 | SageMaker endpoints + model registry wiring | 46–48 |
| 12 | Agent Spine integration test (replaces stubs) | 49 |
| 13 | Model cards + AI-Act documentation | 50 |

---

## Phase 0: Bootstrap

### Task 1: Repo + deps

```bash
mkdir trax-io-forecasting && cd trax-io-forecasting
git init && uv init --python 3.12 --package
```

`pyproject.toml` (abbreviated):
```toml
[project]
name = "trax-io-forecasting"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "trax-io-agent-spine>=0.1.0",        # for contracts + Specialist base
  "statsforecast>=1.7.0",
  "lightgbm>=4.3.0",
  "chronos-forecasting>=1.2.0",
  "torch>=2.3.0",                       # Chronos backbone
  "scipy>=1.13.0",
  "pyiceberg[glue,s3fs,pyarrow]>=0.7.0",
  "pydantic>=2.7.0",
  "structlog>=24.1.0",
  "sagemaker>=2.220.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2.0",
  "pytest-cov>=5.0.0",
  "hypothesis>=6.100.0",
  "ruff>=0.4.0",
  "mypy>=1.10.0",
]
```

### Task 2: Demand fixtures covering every regime and edge case

`tests/fixtures/demand_cases.py` ships 30+ canonical demand patterns: one-shot AOG event, steadily growing demand, seasonal utilization swing, lead-time collapse, vendor change, AD-driven demand shock, zero-demand with phantom historical signal. Each fixture is deterministic, parameterized by a seed, and used across every forecasting and policy test.

### Task 3: Lead-time fixtures

`tests/fixtures/lead_time_cases.py` — distributions for key vendors matching real MRO patterns: nominal (gamma mean=21, CV=0.3), drifted (promised=14, realized=28 with high variance), bimodal (fast lane + slow lane), and regressed (recent deterioration).

### Task 4: CI with ruff + mypy + pytest + hypothesis

---

## Phase 1: Service-level math primitives

These are the math bricks the policy engine stacks. Bugs here cascade silently into wrong stock levels.

### Task 5: Normal approximation service-level math

**Files:** `src/trax_io_forecasting/policy/service_level.py`, `tests/unit/policy/test_service_level.py`

- [ ] **Failing test** — given a mean lead-time demand of 100 and σ=20, a 95% fill-rate target requires a safety factor such that the implied stockout probability is 5%.

```python
# tests/unit/policy/test_service_level.py (excerpt)
import math
from trax_io_forecasting.policy.service_level import (
    safety_stock_normal, z_for_fill_rate,
)


def test_z_for_95pct():
    assert math.isclose(z_for_fill_rate(0.95), 1.6449, abs_tol=1e-3)


def test_safety_stock_normal_matches_textbook():
    # μ_LTD = 100, σ_LTD = 20, target = 95% → SS = z × σ = 1.645 × 20 ≈ 33
    ss = safety_stock_normal(sigma_ltd=20.0, service_level=0.95)
    assert math.isclose(ss, 32.9, abs_tol=0.5)
```

- [ ] **Implement**

```python
# src/trax_io_forecasting/policy/service_level.py
from __future__ import annotations
from scipy.stats import norm


def z_for_fill_rate(fill_rate: float) -> float:
    """Return the z-score for a single-period cycle-service-level target."""
    if not 0.0 < fill_rate < 1.0:
        raise ValueError(f"fill_rate must be in (0,1), got {fill_rate}")
    return float(norm.ppf(fill_rate))


def safety_stock_normal(*, sigma_ltd: float, service_level: float) -> float:
    return z_for_fill_rate(service_level) * sigma_ltd
```

### Task 6: NBD and compound-Poisson service-level math

For intermittent demand, normal approximations break down at the tails. Implement the negative-binomial and compound-Poisson methods: given a demand distribution and a lead-time distribution, return the reorder point that achieves the target fill rate via numerical quantile inversion.

### Task 7: Lead-time-demand convolution

**Files:** `src/trax_io_forecasting/policy/lead_time.py`

Convolve a per-period demand distribution with a lead-time distribution to produce a lead-time-demand (LTD) distribution. Two code paths:
- **Fast path**: normal approximation when both inputs are near-normal (sum of means, sum of variances + cross-term).
- **Slow path**: numerical convolution for compound-Poisson demand and gamma lead time.

### Task 8: Unit service-level tests against textbook cases

Property-based tests (hypothesis) assert monotonicity: increasing the service-level target can never decrease the safety stock; increasing the lead-time variance can never decrease the safety stock.

---

## Phase 2: Classical intermittent forecasters

### Task 9: `ClassicalForecastModel` wrapping `statsforecast`

**Files:** `src/trax_io_forecasting/models/classical.py`

Wrap `statsforecast.models.CrostonClassic`, `CrostonOptimized`, `TSB`, `IMAPA` behind a uniform `ForecastingModel` Protocol:

```python
# src/trax_io_forecasting/models/base.py
from typing import Protocol
from trax_io.contracts.demand import DemandHistory
from trax_io.contracts.forecast import ForecastDistribution, ForecastRequest


class ForecastingModel(Protocol):
    model_id: str
    model_version: str

    def fit(self, history: DemandHistory) -> None: ...
    def forecast(self, *, request: ForecastRequest) -> ForecastDistribution: ...
```

```python
# src/trax_io_forecasting/models/classical.py (abbreviated)
from __future__ import annotations
from statsforecast import StatsForecast
from statsforecast.models import CrostonOptimized, TSB, IMAPA
from trax_io.contracts.demand import DemandHistory
from trax_io.contracts.forecast import ForecastDistribution, ForecastRequest


class CrostonModel:
    model_id = "classical.croston_optimized"
    model_version = "1.7.0"

    def fit(self, history: DemandHistory) -> None:
        df = _history_to_dataframe(history)
        self._fit = StatsForecast(models=[CrostonOptimized()], freq="D", n_jobs=1)
        self._fit.fit(df)

    def forecast(self, *, request: ForecastRequest) -> ForecastDistribution:
        y = self._fit.predict(h=int(request.horizon))
        mean = float(y["CrostonOptimized"].mean())
        # Croston emits only the point forecast; derive spread via residuals
        residuals = self._fit.models_[0].residuals_
        sigma = float(residuals.std())
        variance = sigma ** 2
        return ForecastDistribution(
            mean=mean, variance=variance,
            p50=mean, p95=mean + 1.645 * sigma, p99=mean + 2.326 * sigma,
            model_id=self.model_id, model_version=self.model_version,
        )
```

### Tasks 10–12: TSB, SBA, IMAPA + Syntetos/Boylan/Kourentzes classification grid

The classifier picks between Croston / TSB / SBA based on the Syntetos-Boylan-Kourentzes rule (ADI, CV²). Tests assert correct model selection on representative demand series.

---

## Phase 3: Compound-Poisson + empirical-Bayes priors

### Task 13: Compound-Poisson fitter

For ultra-rare parts, assume demand = Poisson(λ) × Geometric(p): inter-arrival times Poisson, demand-given-arrival geometric. Fit via method of moments + MLE.

### Task 14: Empirical-Bayes priors

**Files:** `src/trax_io_forecasting/features/priors.py`

When history is too sparse to fit alone (< 6 observations in 24 months), borrow strength from peer parts in the same `(ATA chapter, criticality tier, fleet)` group. Hierarchical Bayesian treatment: peer prior on λ, tenant-specific posterior from the few observations available.

```python
# src/trax_io_forecasting/features/priors.py (abbreviated)
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from scipy.stats import gamma


@dataclass(frozen=True)
class DemandPrior:
    alpha: float  # gamma shape
    beta: float   # gamma rate
    peer_group_id: str
    n_peers: int


def posterior_lambda(
    *, prior: DemandPrior, observed_events: int, observed_days: int
) -> tuple[float, float]:
    """Return (posterior_alpha, posterior_beta) for Poisson rate λ.

    Gamma-Poisson conjugate update:
      alpha_post = alpha_prior + events
      beta_post  = beta_prior + days
    """
    return (prior.alpha + observed_events, prior.beta + observed_days)
```

### Task 15: `UltraRareModel` combining compound-Poisson + EB priors

### Task 16: Fallback behavior when peer group is empty

When a part has no peer parts (entirely new fleet, unprecedented ATA chapter), the model refuses to forecast and emits a `ForecastConfidence.INSUFFICIENT` signal. The Agent Spine's orchestrator detects this and falls back to the tenant's current `PN_INVENTORY_LEVEL` value.

---

## Phase 4: LightGBM with causal covariates

### Task 17: Feature engineering for the causal model

**Files:** `src/trax_io_forecasting/features/causal.py`

Given a `(tenant_id, pn, location, target_date)`, materialize:
- Trailing demand counts (7d, 30d, 90d, 180d, 365d).
- Wash rate (monthly, quarterly).
- Lead-time distribution stats.
- Causal features: flight hours per fleet (7d, 30d, 90d), cycles (7d, 30d, 90d), destination mix, tail count, seasonality harmonics.
- Part attributes: criticality, ATA, repair cost, market unit cost.
- EO signal: count of open `eo_published` events for fleet in last 180 days.

Target: demand in the next 30/60/90 days.

### Task 18: `LightGbmDemandModel` training

SageMaker training job with quantile regression objective (quantile=0.5 for point, 0.95 and 0.99 for tails) to directly emit the distributional forecast the policy engine needs.

### Task 19: Real-time inference endpoint

SageMaker real-time endpoint for sub-100ms inference. Multi-tenant model serving uses a single endpoint with tenant-specific models loaded from S3 at request time (or warm cache).

### Task 20: Model-fit diagnostics

Plots checked in CI:
- Calibration: does the 95% quantile actually cover 95% of realized demand?
- Bias per regime and per criticality.
- Feature importance per regime.

### Task 21: TSB fallback guardrail

When LightGBM's predicted distribution deviates > 2σ from TSB's distribution on the same series, emit a `ForecastConfidence.LOW` flag and the orchestrator routes to Tier A (advisor).

---

## Phase 5: Chronos foundation-model challenger

### Task 22: Chronos wrapper conforming to `ForecastingModel` Protocol

Zero-shot forecasting on the full multi-tenant demand corpus. Chronos serves the `high_volume` challenger role and the `ultra_rare` zero-shot challenger role.

### Task 23: Chronos fine-tune on de-identified cross-tenant features

`src/trax_io_forecasting/training/chronos_finetune.py` runs in the isolated federated-training AWS account (per §5.3 of the design). Zero access to tenant PII; features are de-identified PN-level aggregates keyed by `(ATA, criticality, fleet type)`.

### Task 24: Per-tenant holdout evaluation for Chronos

Before a Chronos champion is ever promoted, the challenger must beat the current champion on a per-tenant rolling 60-day holdout for 45 consecutive days. No cross-tenant evaluation (every tenant's champion is evaluated against its own data).

---

## Phase 6: Ensemble + model registry

### Task 25: Weighted-MAPE ensemble for `high_volume`

**Files:** `src/trax_io_forecasting/models/ensemble.py`

Ensemble = weighted combination of LightGBM + Chronos, weights = normalized inverse-MAPE over the trailing 30 days. Re-weighted daily. Smooths catastrophic single-model failures.

### Task 26: Model registry client

**Files:** `src/trax_io_forecasting/models/registry.py`

Wraps SageMaker Model Registry:
- Every trained model version is registered with lineage back to training dataset, training-job hash, approver.
- Champion pointer per `(tenant_id, regime)` is stored in a DynamoDB table with version history.
- Promotions are append-only; rollback is a pointer update.

Every recommendation's `provenance_id` resolves through the registry to the exact model version that produced it. SOC 2 and AI-Act compatible.

### Task 27: Atomic champion switch

Switching the champion pointer is transactional (DynamoDB conditional write). Tests assert no reader sees a torn champion state during a switch.

---

## Phase 7: Policy Engine

The Policy Engine is the deterministic layer planners sign off on.

### Task 28: Base-stock policy `(S-1, S)`

**Files:** `src/trax_io_forecasting/policy/base_stock.py`

For `ultra_rare + essentiality ≤ 2`. Given lead-time demand distribution and service-level target, compute `S` = smallest integer such that `P(LTD > S) ≤ 1 − target`.

### Task 29: `(s, S)` continuous-review policy

For `intermittent`. Wilson EOQ adjusted for LTD distribution: `EOQ = √(2 × D × K / h)` where `K` is the ordering cost estimate (per tenant) and `h` is the holding cost rate. `s = ROP`, `S = ROP + EOQ`. Tests verify Wilson equivalence when LTD is deterministic.

### Task 30: `(R, Q)` periodic-review policy

For `moderate` and `high_volume`. Review period `R` aligned to the vendor review cycle from `pn_vendor_price.lead_days`. `Q = EOQ` with `MinOQ` floor.

### Task 31: Service-level target routing

Per design §5.5, the table:

| Essentiality | Fill rate |
|---|---|
| 1 | 99.5% |
| 2 | 98% |
| 3 | 95% |
| 4 | 92% |
| 5 | 90% |

is tenant-overridable. The Policy Engine reads the effective target from the `essentiality_mapping` feature-store row.

### Task 32: `PolicyEngine` facade

**Files:** `src/trax_io_forecasting/policy/engine.py`

```python
# src/trax_io_forecasting/policy/engine.py (abbreviated)
from __future__ import annotations
from trax_io.contracts.forecast import ForecastDistribution
from trax_io.contracts.policy import PolicyKind, PolicyRecommendation
from trax_io.contracts.regime import Regime
from trax_io.contracts.tenant import CanonicalCriticality
from trax_io_forecasting.policy.base_stock import compute_base_stock
from trax_io_forecasting.policy.s_S import compute_s_S
from trax_io_forecasting.policy.R_Q import compute_R_Q
from trax_io_forecasting.policy.provenance import ProvenanceRecord


class PolicyEngine:
    def recommend(
        self,
        *,
        tenant_id: str,
        pn: str,
        location: str,
        regime: Regime,
        criticality: CanonicalCriticality,
        forecast: ForecastDistribution,
        lead_time_mean_days: float,
        lead_time_variance_days: float,
        service_level_target: float,
        min_order_qty: int,
        ordering_cost: float,
        holding_cost_rate: float,
        unit_cost: float,
    ) -> PolicyRecommendation:
        # Compute LTD distribution
        ltd_mean, ltd_sigma = _convolve(forecast, lead_time_mean_days, lead_time_variance_days)

        if regime == Regime.ULTRA_RARE and criticality <= CanonicalCriticality.TIER_2:
            rop, eoq, ss, max_stock, kind = compute_base_stock(
                ltd_mean=ltd_mean, ltd_sigma=ltd_sigma,
                service_level=service_level_target,
            )
        elif regime == Regime.INTERMITTENT:
            rop, eoq, ss, max_stock, kind = compute_s_S(
                ltd_mean=ltd_mean, ltd_sigma=ltd_sigma,
                service_level=service_level_target,
                ordering_cost=ordering_cost, holding_cost_rate=holding_cost_rate,
                unit_cost=unit_cost, min_order_qty=min_order_qty,
            )
        else:
            rop, eoq, ss, max_stock, kind = compute_R_Q(
                ltd_mean=ltd_mean, ltd_sigma=ltd_sigma,
                service_level=service_level_target,
                ordering_cost=ordering_cost, holding_cost_rate=holding_cost_rate,
                unit_cost=unit_cost, min_order_qty=min_order_qty,
                review_period_days=14,
            )

        provenance = ProvenanceRecord.build(
            model=forecast.model_id, model_version=forecast.model_version,
            regime=regime, service_level=service_level_target,
            ltd_mean=ltd_mean, ltd_sigma=ltd_sigma,
        )

        return PolicyRecommendation(
            tenant_id=tenant_id, pn=pn, location=location,
            rop=rop, eoq=eoq, safety_stock=ss, max_stock=max_stock,
            policy_kind=kind,
            service_level_target=service_level_target,
            provenance_id=provenance.provenance_id,
            model_id=f"{forecast.model_id}/{forecast.model_version}",
        )
```

### Task 33: Policy engine invariants (property tests)

Hypothesis tests:
- `rop ≥ safety_stock` always.
- `max_stock ≥ rop + eoq` always.
- Increasing `service_level_target` monotonically increases `safety_stock`.
- Increasing `lead_time_variance_days` monotonically increases `safety_stock`.
- `eoq ≥ min_order_qty` always.

---

## Phase 8: Interchangeability rollup + constraints

### Task 34: Interchangeability rollup

**Files:** `src/trax_io_forecasting/policy/interchange.py`

Given an `InterchangeGroup`, sum demand across all two-way members (honoring one-way chains as directed edges), run the policy calc against the aggregate, then apportion `ROP`, `EOQ`, `SS`, `Max` back to individual PNs proportional to trailing 12-month consumption.

### Task 35: Hard-cap constraints

**Files:** `src/trax_io_forecasting/policy/constraints.py`

- Shelf-life: `max_stock × avg_daily_demand ≤ 0.6 × shelf_life_days`.
- Hazmat: `max_stock ≤ 2 × current_max` per write cycle.
- Tool-control: same.
- `eoq ≥ min_order_qty` from `pn_vendor_price`.

Constraints run after `PolicyEngine.recommend()` and are allowed to tighten (never loosen) the recommendation. If tightening would violate `rop ≥ safety_stock` or similar, emit a `PolicyConstraintViolation` and escalate to advisor tier — same channel the Guardrail Agent already uses.

### Task 36: Open-order awareness

If open orders for `(pn, location)` already push current + pending stock above proposed `max_stock`, the policy engine defers the write one cycle rather than forcing a decrement.

---

## Phase 9: Forecasting + Policy Strands specialists

### Task 37: `ForecastingAgent` — replaces `StubForecastingAgent`

**Files:** `src/trax_io_forecasting/forecasting_agent.py`

```python
# src/trax_io_forecasting/forecasting_agent.py (abbreviated)
from trax_io.contracts.forecast import ForecastDistribution, ForecastRequest
from trax_io.contracts.regime import Regime
from trax_io.specialists.base import Specialist
from trax_io_forecasting.models.registry import ModelRegistry


class ForecastingAgent(Specialist):
    def __init__(self, *, registry: ModelRegistry) -> None:
        super().__init__(specialist_name="forecasting")
        self._registry = registry

    def forecast(
        self, *, request: ForecastRequest, regime: Regime, mean_history: float,
    ) -> ForecastDistribution:
        self._assert_tenant_match(request.tenant_id)
        champion = self._registry.get_champion(tenant_id=request.tenant_id, regime=regime)
        model = champion.load()
        # History load delegated to feature store loader
        history = self._registry.feature_loader.load_history(
            tenant_id=request.tenant_id, pn=request.pn, location=request.location,
        )
        model.fit(history)
        distribution = model.forecast(request=request)
        self._log.info(
            "forecast_produced",
            tenant=request.tenant_id, pn=request.pn, regime=regime.value_str,
            model=distribution.model_id, mean=distribution.mean,
        )
        return distribution
```

### Task 38: `PolicyEngineAgent` — replaces `StubPolicyEngineAgent`

Wraps `PolicyEngine` with `Specialist` tenant-enforcement, structured logging, and provenance-recording.

### Task 39: Forecast confidence gating

`ForecastDistribution` gains a `confidence: Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]` field. `INSUFFICIENT` means the orchestrator falls back to the tenant's current `PN_INVENTORY_LEVEL` value. `LOW` forces Tier A regardless of Guardrail Agent's autonomy decision.

### Task 40: Real Agent Spine integration

`SupervisorOrchestrator` construction in production wires `ForecastingAgent` and `PolicyEngineAgent` (this plan) instead of the stubs shipped in sub-plan #4 Task 29. Stubs remain in the Spine repo for test-only consumption.

---

## Phase 10: Evaluation pipeline + champion/challenger promotion

### Task 41: Rolling 60-day holdout splitter

**Files:** `src/trax_io_forecasting/evaluation/holdout.py`

For each `(tenant_id, pn, location)`, generate daily holdout splits: train on days `[-∞, -60]`, score on days `[-60, -1]`.

### Task 42: Evaluation metrics

**Files:** `src/trax_io_forecasting/evaluation/metrics.py`

- **Weighted MAPE** — weighted by `unit_cost × criticality_weight`. Dollar-weighted error matters, raw MAPE does not.
- **Realized vs. target fill rate** — for each policy produced and subsequently "in force" for a period, compute realized stockouts per criticality tier and compare to target.
- **Total cost delta** — holding cost + ordering cost + stockout-proxy cost, vs. a counterfactual "kept static levels" baseline built from the tenant's pre-agent `PN_INVENTORY_LEVEL` history.
- **Planner override rate** — rate at which Tier-A recommendations are rejected or modified. Derived from eMRO's `PN_INVENTORY_LEVEL_HISTORY`.
- **Planner trust score** — composite weighted metric.

### Task 43: Nightly evaluation Glue job

Scores every champion and every challenger against the rolling holdout. Writes per-model daily scores to an Iceberg `evaluation_scores` table.

### Task 44: Auto-promotion gate

**Files:** `src/trax_io_forecasting/evaluation/champion_challenger.py`

A challenger is auto-promoted only when:
1. It beats the current champion on all three metrics (weighted MAPE, realized fill rate, total cost) for 45 consecutive days.
2. A planner-visible change notice is posted to the Planner UI (sub-plan #7) 14 days before promotion.
3. No human rejection during the notice window.

Promotion is an atomic pointer update (Task 27). Demotion happens automatically if a new champion regresses >10% on weighted MAPE for 7 consecutive days.

### Task 45: Per-tenant scoreboard

Feeds the Business Value Report (sub-plan #8) and an internal Trax-side dashboard.

---

## Phase 11: SageMaker endpoints

### Tasks 46–48

CDK stacks provisioning SageMaker real-time endpoints for LightGBM + Chronos, model registry, multi-model endpoints for tenant-specific LightGBM variants, per-tenant invocation metrics and cost attribution feeding the Spine's `CostLedger`.

---

## Phase 12: Agent Spine integration test

### Task 49: End-to-end against real models

Replaces the stub-backed integration test in sub-plan #4 Task 35 with a real-model version. Seeds the feature store with one tenant of realistic data (via sub-plan #1 fixtures), runs the full `SupervisorOrchestrator.optimize()` end-to-end with real `ForecastingAgent` + `PolicyEngineAgent`, asserts output consistency properties (not specific values — real ML should not be pinned to magic numbers in tests).

---

## Phase 13: Model cards + AI-Act documentation

### Task 50: Model cards per production model

**Files:** `docs/MODEL_CARDS.md`

For each model shipped (Croston, TSB, CompoundPoisson+EB, LightGBM, Chronos, Ensemble):
- Intended use and constraints.
- Training data description (with sampling dates and tenant provenance).
- Evaluation metrics and holdout methodology.
- Known limitations (e.g., "Chronos degrades on zero-demand series with phantom historical signal").
- Failure modes and their telemetry.
- Version history and approval chain.

This is the document the first SOC 2 Type II auditor asks for and the EU AI Act high-risk-system documentation template.

---

## Self-Review

| Spec section | Covered by |
|---|---|
| §5.1 Regime Router (in Spine) | Not here — owned by sub-plan #4 |
| §5.2 Four regime-specific champion models + Chronos challenger | Phases 2–5 |
| §5.3 Federated cross-tenant training (de-identified) | Task 23 |
| §5.4 Policy Engine algorithms by regime | Phase 7 |
| §5.4 Interchangeability rollup + shelf-life/hazmat/tool clamps | Phase 8 |
| §5.5 Service-level defaults, tenant-overridable | Task 31 |
| §5.6 Champion/challenger 45-day promotion gate | Task 44 |
| Model registry lineage for SOC 2 / AI-Act | Tasks 26, 50 |
| Forecast confidence gating into Tier A fallback | Task 39 |
| Real replacement of sub-plan #4 stubs | Tasks 37–40 + 49 |

**Estimated team:** 1 ML engineering lead + 2 ML engineers + 1 ML platform engineer + 0.5 statistician consultant for intermittent-demand modeling review = ~14 weeks elapsed.

**Critical path note:** Phase 7 (Policy Engine) is the gate the rest of the organization waits on. It is pure Python and highly testable — worth front-loading onto the strongest engineer on the team. Phases 4–6 (ML models) can start in parallel and swap in over the following months.
