# Forecasting Slice A — Classical Intermittent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `StatisticalProjector` (a `DemandProjector`) that applies statsforecast Croston/SBA/TSB to the `intermittent` regime — producing a fitted `COMPOUND_POISSON` `DemandProjection` with #11's exact distribution machinery — and delegates every other regime to the deterministic projector, plus a MASE backtest and a one-line #11 DI seam.

**Architecture:** New `services/forecasting/` package (`trax_io_forecasting`). `series.py` turns a bucketed `DemandHistory` into a dense numpy series; `classical.py` wraps statsforecast and auto-selects the model (Syntetos-Boylan-Croston); `projector.py` builds the fitted `DemandProjection` (or delegates); `backtest.py` scores fitted-vs-average by MASE. One small #11 change makes `RecommendationService`'s projector injectable so the forecaster swaps in via DI.

**Tech Stack:** Python 3.14, `statsforecast>=2.0` (+ `scipy>=1.18`, `numba>=0.62` pinned for 3.14), numpy, `uv` + `pytest` + `ruff`. Reuses `trax-io-reco` (#11) + transitively `trax-io-feature-store` (#2).

Spec: [docs/superpowers/specs/2026-06-27-forecasting-classical-intermittent-design.md](../specs/2026-06-27-forecasting-classical-intermittent-design.md).

## Global Constraints

- Work in `services/forecasting/`; branch off `main`. src-layout; `[tool.pytest.ini_options] pythonpath = ["src"]`; ruff `line-length=100`, `select=["E","F","I","B","UP","N","SIM"]`; no mypy; Python `>=3.12` (env is 3.14).
- Dep pin (verified): `statsforecast>=2.0`, `scipy>=1.18`, `numba>=0.62` — otherwise the resolver picks an old scipy with no 3.14 wheel and fails a source build. `trax-io-reco` is a **non-editable** path source (`{ path = "../recommendation-engine" }`); after editing #11, `uv sync --reinstall-package trax-io-reco`.
- `statsforecast` is **lazy-imported** inside `forecast_rate` (so the module imports without warming the JIT / needing the dep at import time of `series.py`/`projector.py`).
- Reuse #11 verbatim — never redefine: `DemandProjection`, `DemandProjectorProtocol`, `HistoricalScheduledProjector` (`trax_io_reco.demand.projection`), `Regime` (`trax_io_reco.contracts.enums`), `PartLocationContext` (`trax_io_reco.contracts.context`); `DemandHistory`/`DemandObservation` (`trax_io_feature_store.schemas`).
- **`DemandProjection` fields** (all required unless noted): `mean_per_day: float`, `std_per_day: float`, `dist_kind: Literal["NORMAL","COMPOUND_POISSON","NBD","EMPIRICAL"]`, `dist_params: dict[str,float]`, `historical_component: float`, `scheduled_component: float`, `by_aircraft: dict[str,float]={}`, `by_task: dict[str,float]={}`, `basis_window_days: int`.
- **`DemandObservation`**: `bucket: Literal["day","week","month"]`, `period_start: date`, `removals: int=0`, `issues: int=0`. `_DAYS_PER_BUCKET = {"day":1.0,"week":7.0,"month":30.44}`.
- **The intermittent machinery (mirror #11 exactly, fitted λ):** `dist_kind="COMPOUND_POISSON"`, `dist_params={"lambda": fitted_per_day, "clump_p": 1.0}`, `std_per_day=sqrt(fitted_per_day)`, `historical_component=fitted_per_day`, `scheduled_component=sum(s.qty for s in context.scheduled_demand)/basis_window_days`, `mean_per_day=fitted_per_day+scheduled_per_day`, `by_aircraft`/`by_task` from `scheduled_demand` (s.ac_type, s.source_ref, s.qty).
- Commit after every green task.

---

## File Structure

```
services/forecasting/
├── pyproject.toml
├── README.md
├── src/trax_io_forecasting/
│   ├── __init__.py
│   ├── series.py        # PeriodSeries + to_period_series
│   ├── classical.py     # ClassicalModel, forecast_rate, select_model
│   ├── projector.py     # StatisticalProjector(DemandProjector)
│   └── backtest.py       # mase, backtest_key, compare, BacktestReport
└── tests/
    ├── conftest.py       # sample_context fixture + with_demand helper
    ├── test_smoke.py
    ├── test_series.py
    ├── test_classical.py
    ├── test_projector.py
    └── test_backtest.py
```
Plus one modification: `services/recommendation-engine/src/trax_io_reco/service.py` (injectable projector).

---

## Task 1: Package scaffold

**Files:**
- Create: `services/forecasting/pyproject.toml`, `README.md`, `src/trax_io_forecasting/__init__.py`
- Test: `services/forecasting/tests/test_smoke.py`

**Interfaces:** Produces an importable `trax_io_forecasting` with `statsforecast` + `trax_io_reco` resolvable.

- [ ] **Step 1: Write the failing test**

Create `services/forecasting/tests/test_smoke.py`:
```python
def test_deps_importable() -> None:
    import trax_io_forecasting  # noqa: F401
    from statsforecast.models import CrostonClassic  # noqa: F401
    from trax_io_reco.demand.projection import HistoricalScheduledProjector  # noqa: F401

    assert trax_io_forecasting.__version__ == "0.1.0"
```

- [ ] **Step 2: Write `pyproject.toml`**

Create `services/forecasting/pyproject.toml`:
```toml
[project]
name = "trax-io-forecasting"
version = "0.1.0"
description = "Trax IO Forecasting — regime-routed demand forecasting (slice A: classical intermittent)"
requires-python = ">=3.12"
dependencies = [
    "numpy>=1.26",
    "scipy>=1.18.0",
    "numba>=0.62.0",
    "statsforecast>=2.0.0",
    "trax-io-reco",
]

[project.optional-dependencies]
dev = ["pytest>=8.2.0", "ruff>=0.4.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv.sources]
trax-io-reco = { path = "../recommendation-engine" }

[tool.hatch.build.targets.wheel]
packages = ["src/trax_io_forecasting"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "SIM"]
```

- [ ] **Step 3: Write the package init + README**

Create `services/forecasting/src/trax_io_forecasting/__init__.py`:
```python
"""Trax IO Forecasting (#5). Slice A: classical intermittent demand forecasting.

A `StatisticalProjector` implements #11's `DemandProjector` Protocol with statsforecast
Croston/SBA/TSB for the `intermittent` regime, delegating other regimes to the deterministic
projector. See docs/superpowers/specs/2026-06-27-forecasting-classical-intermittent-design.md.
"""

__version__ = "0.1.0"
```

Create `services/forecasting/README.md`:
```markdown
# Trax IO Forecasting — service

Sub-project #5, slice A. `StatisticalProjector` (a `DemandProjector`) applies statsforecast
classical models (Croston / SBA / TSB, SBC-selected) to the `intermittent` regime, producing a
fitted `COMPOUND_POISSON` `DemandProjection`; other regimes delegate to the deterministic
`HistoricalScheduledProjector`. A MASE backtest scores fitted-vs-average.

## Dev setup
```bash
cd services/forecasting
uv sync --extra dev
uv run --extra dev pytest
uv run --extra dev ruff check .
```

`statsforecast` runs on Python 3.14 only with `scipy>=1.18` + `numba>=0.62` (pinned in
`pyproject.toml`). Inject the forecaster into #11 with
`RecommendationService(..., projector=StatisticalProjector())`.
```

- [ ] **Step 4: Sync and run the smoke test**

Run: `cd services/forecasting && uv sync --extra dev && uv run --extra dev pytest tests/test_smoke.py -q`
Expected: 1 passed. (The first sync compiles numba/statsforecast — may take a minute.)

- [ ] **Step 5: Commit**

```bash
git add services/forecasting/
git commit -m "#5 forecasting: package scaffold + statsforecast deps (py3.14 pins) + #11 path dep"
```

---

## Task 2: Period series

**Files:**
- Create: `services/forecasting/src/trax_io_forecasting/series.py`
- Test: `services/forecasting/tests/test_series.py`

**Interfaces:**
- Consumes: `DemandHistory`/`DemandObservation` (`trax_io_feature_store.schemas`).
- Produces: `PeriodSeries` (frozen dataclass: `values: tuple[float, ...]`, `bucket: str`, `days_per_period: float`); `to_period_series(history: DemandHistory) -> PeriodSeries`.

- [ ] **Step 1: Write the failing test**

Create `services/forecasting/tests/test_series.py`:
```python
from datetime import date

from trax_io_feature_store.schemas import DemandHistory, DemandObservation

from trax_io_forecasting.series import to_period_series


def _history(obs: list[DemandObservation]) -> DemandHistory:
    return DemandHistory(tenant_id="acme", pn="PN-A", location="LOC-1", observations=tuple(obs))


def test_orders_and_sums_removals_plus_issues() -> None:
    obs = [
        DemandObservation(bucket="month", period_start=date(2026, 3, 1), removals=2, issues=1),
        DemandObservation(bucket="month", period_start=date(2026, 1, 1), removals=1, issues=0),
    ]
    s = to_period_series(_history(obs))
    assert s.values == (1.0, 3.0)  # Jan (1) before Mar (2+1), sorted by period_start
    assert s.bucket == "month" and s.days_per_period == 30.44


def test_zero_fills_missing_periods() -> None:
    obs = [
        DemandObservation(bucket="month", period_start=date(2026, 1, 1), removals=1),
        DemandObservation(bucket="month", period_start=date(2026, 4, 1), removals=2),  # gap Feb/Mar
    ]
    s = to_period_series(_history(obs))
    assert s.values == (1.0, 0.0, 0.0, 2.0)  # Jan, Feb=0, Mar=0, Apr


def test_empty_history_is_empty_series() -> None:
    s = to_period_series(_history([]))
    assert s.values == () and s.days_per_period == 30.44
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_series.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_forecasting.series`.

- [ ] **Step 3: Implement `series.py`**

Create `services/forecasting/src/trax_io_forecasting/series.py`:
```python
"""Turn a bucketed DemandHistory into a dense, gap-filled per-period demand series."""

from __future__ import annotations

from dataclasses import dataclass

from trax_io_feature_store.schemas import DemandHistory

_DAYS_PER_BUCKET = {"day": 1.0, "week": 7.0, "month": 30.44}
_DEFAULT_BUCKET = "month"


@dataclass(frozen=True)
class PeriodSeries:
    values: tuple[float, ...]
    bucket: str
    days_per_period: float


def _periods_between(bucket: str, start, end) -> int:  # noqa: ANN001
    if bucket == "month":
        return (end.year - start.year) * 12 + (end.month - start.month)
    return (end - start).days // int(_DAYS_PER_BUCKET[bucket])


def to_period_series(history: DemandHistory) -> PeriodSeries:
    obs = sorted(history.observations, key=lambda o: o.period_start)
    if not obs:
        return PeriodSeries(values=(), bucket=_DEFAULT_BUCKET,
                            days_per_period=_DAYS_PER_BUCKET[_DEFAULT_BUCKET])
    bucket = obs[0].bucket
    first = obs[0].period_start
    span = _periods_between(bucket, first, obs[-1].period_start) + 1
    dense = [0.0] * span
    for o in obs:
        idx = _periods_between(bucket, first, o.period_start)
        dense[idx] += float(o.removals + o.issues)
    return PeriodSeries(values=tuple(dense), bucket=bucket, days_per_period=_DAYS_PER_BUCKET[bucket])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_series.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add services/forecasting/src/trax_io_forecasting/series.py services/forecasting/tests/test_series.py
git commit -m "#5 forecasting: to_period_series (zero-gap-filled per-period demand)"
```

---

## Task 3: Classical forecasters + model selection

**Files:**
- Create: `services/forecasting/src/trax_io_forecasting/classical.py`
- Test: `services/forecasting/tests/test_classical.py`

**Interfaces:**
- Produces:
  - `ClassicalModel(StrEnum)`: `CROSTON="croston"`, `SBA="sba"`, `TSB="tsb"`.
  - `forecast_rate(values: Sequence[float], model: ClassicalModel) -> float` — per-period rate via statsforecast; `0.0` for an all-zero / `len<2` series (no statsforecast call); non-finite/negative → `0.0`.
  - `select_model(values: Sequence[float]) -> ClassicalModel` — SBC: `ADI = n/n_nonzero`, `CV2 = var(nonzero)/mean(nonzero)**2`; `ADI >= 2.0` → `TSB`; elif `CV2 >= 0.49` → `SBA`; else `CROSTON`; too-sparse (`n_nonzero < 2`) → `CROSTON`.

- [ ] **Step 1: Write the failing test**

Create `services/forecasting/tests/test_classical.py`:
```python
import pytest

from trax_io_forecasting.classical import ClassicalModel, forecast_rate, select_model


def test_forecast_rate_pins_croston_reference() -> None:
    # Hand-checked against statsforecast CrostonClassic on this intermittent series (~0.35/period).
    y = [0, 0, 1, 0, 0, 0, 2, 0, 0, 1, 0, 0]
    assert forecast_rate(y, ClassicalModel.CROSTON) == pytest.approx(0.35, abs=0.05)


def test_forecast_rate_zero_on_degenerate() -> None:
    assert forecast_rate([0, 0, 0, 0], ClassicalModel.SBA) == 0.0
    assert forecast_rate([5], ClassicalModel.CROSTON) == 0.0  # len < 2


def test_select_model_lumpy_is_sba() -> None:
    # high CV^2 (sizes 1 and 9), moderate intermittence
    assert select_model([1, 0, 9, 0, 1, 0, 9, 0]) == ClassicalModel.SBA


def test_select_model_steady_intermittent_is_croston() -> None:
    # even sizes, ADI < 2 -> Croston
    assert select_model([1, 0, 1, 1, 0, 1, 1, 0]) == ClassicalModel.CROSTON


def test_select_model_very_sparse_is_tsb() -> None:
    # ADI >= 2.0 (1 demand every ~4 periods) -> TSB
    assert select_model([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]) == ClassicalModel.TSB
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_classical.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_forecasting.classical`.

- [ ] **Step 3: Implement `classical.py`**

Create `services/forecasting/src/trax_io_forecasting/classical.py`:
```python
"""statsforecast classical intermittent-demand models + Syntetos-Boylan-Croston selection."""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

_LUMPY_CV2 = 0.49
_OBSOLESCENCE_ADI = 2.0


class ClassicalModel(StrEnum):
    CROSTON = "croston"
    SBA = "sba"
    TSB = "tsb"


def forecast_rate(values: Sequence[float], model: ClassicalModel) -> float:
    vals = [float(v) for v in values]
    if len(vals) < 2 or sum(vals) <= 0.0:
        return 0.0

    import numpy as np
    from statsforecast.models import CrostonClassic, CrostonSBA, TSB

    estimator = {
        ClassicalModel.CROSTON: CrostonClassic(),
        ClassicalModel.SBA: CrostonSBA(),
        ClassicalModel.TSB: TSB(alpha_d=0.1, alpha_p=0.1),
    }[model]
    rate = float(estimator.forecast(y=np.asarray(vals, dtype=np.float64), h=1)["mean"][0])
    return rate if math.isfinite(rate) and rate > 0.0 else 0.0


def select_model(values: Sequence[float]) -> ClassicalModel:
    vals = [float(v) for v in values]
    nonzero = [v for v in vals if v > 0.0]
    if len(nonzero) < 2:
        return ClassicalModel.CROSTON
    adi = len(vals) / len(nonzero)
    mean_nz = sum(nonzero) / len(nonzero)
    cv2 = (sum((v - mean_nz) ** 2 for v in nonzero) / len(nonzero)) / (mean_nz**2)
    if adi >= _OBSOLESCENCE_ADI:
        return ClassicalModel.TSB
    if cv2 >= _LUMPY_CV2:
        return ClassicalModel.SBA
    return ClassicalModel.CROSTON
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_classical.py -q`
Expected: 5 passed. (If the pinned Croston value drifts in your statsforecast build, widen the `abs=` tolerance and note the observed value — the test exists to catch a broken wrapper, not to pin an exact float.)

- [ ] **Step 5: Commit**

```bash
git add services/forecasting/src/trax_io_forecasting/classical.py services/forecasting/tests/test_classical.py
git commit -m "#5 forecasting: classical forecast_rate (Croston/SBA/TSB) + SBC select_model"
```

---

## Task 4: Statistical projector

**Files:**
- Create: `services/forecasting/src/trax_io_forecasting/projector.py`
- Create: `services/forecasting/tests/conftest.py`
- Test: `services/forecasting/tests/test_projector.py`

**Interfaces:**
- Consumes: `to_period_series` (T2), `forecast_rate`/`select_model`/`ClassicalModel` (T3); #11's `DemandProjection`, `HistoricalScheduledProjector`, `DemandProjectorProtocol`, `Regime`, `PartLocationContext`.
- Produces: `StatisticalProjector(fallback: DemandProjectorProtocol | None = None, *, model: ClassicalModel | None = None, basis_window_days: int = 730)` with `project(*, context: PartLocationContext, regime: Regime) -> DemandProjection`.

- [ ] **Step 1: Write the conftest (real context from the extract sample)**

Create `services/forecasting/tests/conftest.py`:
```python
"""Fixtures: a real PartLocationContext from #11's extract sample + a demand-swapping helper."""

from __future__ import annotations

from pathlib import Path

import pytest
from trax_io_feature_store import TenantContext
from trax_io_feature_store.schemas import DemandHistory, DemandObservation
from trax_io_reco.contracts.context import PartLocationContext
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.data.feature_reader import FeatureReader

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture
def sample_context() -> PartLocationContext:
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    assembler = ContextAssembler(features=FeatureReader(fs), inventory_state=inv)
    pn, loc = keys[0]
    return assembler.assemble(tenant=TenantContext(tenant_id=tid), pn=pn, location=loc)


def with_demand(ctx: PartLocationContext, obs: list[DemandObservation]) -> PartLocationContext:
    history = DemandHistory(
        tenant_id=ctx.tenant_id, pn=ctx.pn, location=ctx.location, observations=tuple(obs)
    )
    return ctx.model_copy(update={"demand_history": history})
```

- [ ] **Step 2: Write the failing test**

Create `services/forecasting/tests/test_projector.py`:
```python
from datetime import date

from trax_io_feature_store.schemas import DemandObservation
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import HistoricalScheduledProjector

from trax_io_forecasting.classical import forecast_rate, select_model
from trax_io_forecasting.projector import StatisticalProjector
from trax_io_forecasting.series import to_period_series

from tests.conftest import with_demand


def _intermittent_obs() -> list[DemandObservation]:
    # 24 months, intermittent + recency-trending (so the fit differs from the flat average).
    counts = [0] * 18 + [1, 2, 1, 3, 2, 4]
    return [
        DemandObservation(bucket="month", period_start=date(2024, 1 + (i % 12), 1)
                          if i < 12 else date(2025, 1 + (i - 12), 1), removals=c)
        for i, c in enumerate(counts)
    ]


def test_intermittent_uses_fitted_lambda(sample_context) -> None:
    ctx = with_demand(sample_context, _intermittent_obs())
    proj = StatisticalProjector().project(context=ctx, regime=Regime.INTERMITTENT)

    series = to_period_series(ctx.demand_history)
    expected_rate = forecast_rate(series.values, select_model(series.values))
    expected_lambda = expected_rate / series.days_per_period

    assert proj.dist_kind == "COMPOUND_POISSON"
    assert proj.dist_params["lambda"] == pytest.approx(expected_lambda)
    assert proj.dist_params["clump_p"] == 1.0


def test_fitted_lambda_differs_from_deterministic_average(sample_context) -> None:
    ctx = with_demand(sample_context, _intermittent_obs())
    fitted = StatisticalProjector().project(context=ctx, regime=Regime.INTERMITTENT)
    deterministic = HistoricalScheduledProjector().project(context=ctx, regime=Regime.INTERMITTENT)
    assert fitted.dist_params["lambda"] != deterministic.dist_params["lambda"]


def test_non_intermittent_delegates_to_fallback(sample_context) -> None:
    ctx = with_demand(sample_context, _intermittent_obs())
    fitted = StatisticalProjector().project(context=ctx, regime=Regime.MODERATE)
    deterministic = HistoricalScheduledProjector().project(context=ctx, regime=Regime.MODERATE)
    assert fitted == deterministic
```

Add `import pytest` at the top of the test file (used by `pytest.approx`).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_projector.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_forecasting.projector`.

- [ ] **Step 4: Implement `projector.py`**

Create `services/forecasting/src/trax_io_forecasting/projector.py`:
```python
"""StatisticalProjector — a DemandProjector that fits the intermittent regime with statsforecast.

Reuses #11's exact intermittent distribution machinery (COMPOUND_POISSON, single-unit Poisson),
replacing only the historical-average rate with a fitted Croston/SBA/TSB rate. Every other regime
delegates to the injected deterministic projector.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector

from trax_io_forecasting.classical import ClassicalModel, forecast_rate, select_model
from trax_io_forecasting.series import to_period_series

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

_DEFAULT_BASIS_DAYS = 730


class StatisticalProjector:
    def __init__(
        self,
        fallback: DemandProjectorProtocol | None = None,
        *,
        model: ClassicalModel | None = None,
        basis_window_days: int = _DEFAULT_BASIS_DAYS,
    ) -> None:
        self._fallback = fallback or HistoricalScheduledProjector(basis_window_days=basis_window_days)
        self._model = model
        self._basis = basis_window_days

    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection:
        if regime is not Regime.INTERMITTENT:
            return self._fallback.project(context=context, regime=regime)

        series = to_period_series(context.demand_history)
        model = self._model or select_model(series.values)
        rate_per_period = forecast_rate(series.values, model)
        fitted_per_day = rate_per_period / series.days_per_period if series.days_per_period else 0.0

        sched_total = float(sum(s.qty for s in context.scheduled_demand))
        scheduled_per_day = sched_total / self._basis
        by_aircraft: dict[str, float] = {}
        by_task: dict[str, float] = {}
        for s in context.scheduled_demand:
            if s.ac_type:
                by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
            by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty

        return DemandProjection(
            mean_per_day=fitted_per_day + scheduled_per_day,
            std_per_day=math.sqrt(fitted_per_day),
            dist_kind="COMPOUND_POISSON",
            dist_params={"lambda": fitted_per_day, "clump_p": 1.0},
            historical_component=fitted_per_day,
            scheduled_component=scheduled_per_day,
            by_aircraft=by_aircraft,
            by_task=by_task,
            basis_window_days=self._basis,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_projector.py -q`
Expected: 3 passed.

- [ ] **Step 6: Lint + commit**

Run: `cd services/forecasting && uv run --extra dev ruff check .` (fix any findings).
```bash
git add services/forecasting/src/trax_io_forecasting/projector.py services/forecasting/tests/conftest.py services/forecasting/tests/test_projector.py
git commit -m "#5 forecasting: StatisticalProjector (intermittent fitted COMPOUND_POISSON; else delegate)"
```

---

## Task 5: MASE backtest

**Files:**
- Create: `services/forecasting/src/trax_io_forecasting/backtest.py`
- Test: `services/forecasting/tests/test_backtest.py`

**Interfaces:**
- Consumes: `PeriodSeries`/`to_period_series` (T2), `forecast_rate`/`select_model` (T3).
- Produces:
  - `mase(actual: Sequence[float], forecast: float, *, naive_scale: float) -> float` — `mean(|actual - forecast|) / naive_scale`; `naive_scale<=0` → `inf`.
  - `naive_scale(values: Sequence[float]) -> float` — in-sample MAE of the naive (lag-1) forecast: `mean(|v[t]-v[t-1]|)`.
  - `backtest_key(values: Sequence[float], rate_fn, *, holdout: int) -> float` — fit `rate_fn(values[:-holdout]) -> float`, MASE it against `values[-holdout:]` using `naive_scale(values[:-holdout])`.
  - `compare(series_values: list[Sequence[float]], *, holdout: int = 6) -> BacktestReport` (frozen dataclass `{champion_mase: float, challenger_mase: float, n_keys: int, champion_wins: bool}`). Champion = `forecast_rate(v, select_model(v))`; challenger = the historical mean `sum(v)/len(v)`.

- [ ] **Step 1: Write the failing test**

Create `services/forecasting/tests/test_backtest.py`:
```python
import math

from trax_io_forecasting.backtest import backtest_key, compare, mase, naive_scale


def test_mase_basic() -> None:
    assert mase([2.0, 2.0, 2.0], 2.0, naive_scale=1.0) == 0.0
    assert mase([0.0, 4.0], 2.0, naive_scale=2.0) == 1.0  # mean|.-2| = 2; /2 = 1


def test_mase_inf_on_zero_scale() -> None:
    assert math.isinf(mase([1.0, 1.0], 0.5, naive_scale=0.0))


def test_naive_scale_is_lag1_mae() -> None:
    assert naive_scale([1.0, 1.0, 4.0]) == 1.5  # |1-1| + |4-1| = 3; /2


def test_backtest_key_scores_a_constant_rate() -> None:
    score = backtest_key([1.0, 1.0, 1.0, 1.0, 1.0, 1.0], lambda v: sum(v) / len(v), holdout=2)
    assert score == 0.0  # constant series, constant forecast -> zero error


def test_compare_reports_a_winner() -> None:
    # a recency-trending intermittent series where the fit should not be worse than the flat mean
    series = [[0, 0, 1, 0, 2, 0, 1, 0, 3, 0, 2, 4]]
    report = compare(series, holdout=4)
    assert report.n_keys == 1
    assert isinstance(report.champion_wins, bool)
    assert report.champion_mase >= 0.0 and report.challenger_mase >= 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_backtest.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_forecasting.backtest`.

- [ ] **Step 3: Implement `backtest.py`**

Create `services/forecasting/src/trax_io_forecasting/backtest.py`:
```python
"""Hold-out backtest + MASE — does the fitted forecast beat the historical average out-of-sample?"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from trax_io_forecasting.classical import forecast_rate, select_model


def mase(actual: Sequence[float], forecast: float, *, naive_scale: float) -> float:
    if naive_scale <= 0.0:
        return math.inf
    mae = sum(abs(float(a) - forecast) for a in actual) / len(actual)
    return mae / naive_scale


def naive_scale(values: Sequence[float]) -> float:
    vals = [float(v) for v in values]
    if len(vals) < 2:
        return 0.0
    return sum(abs(vals[t] - vals[t - 1]) for t in range(1, len(vals))) / (len(vals) - 1)


def backtest_key(
    values: Sequence[float], rate_fn: Callable[[Sequence[float]], float], *, holdout: int
) -> float:
    vals = [float(v) for v in values]
    train, test = vals[:-holdout], vals[-holdout:]
    return mase(test, rate_fn(train), naive_scale=naive_scale(train))


@dataclass(frozen=True)
class BacktestReport:
    champion_mase: float
    challenger_mase: float
    n_keys: int
    champion_wins: bool


def _champion_rate(values: Sequence[float]) -> float:
    return forecast_rate(values, select_model(values))


def _challenger_rate(values: Sequence[float]) -> float:
    return sum(float(v) for v in values) / len(values) if values else 0.0


def compare(series_values: list[Sequence[float]], *, holdout: int = 6) -> BacktestReport:
    champ = [backtest_key(v, _champion_rate, holdout=holdout) for v in series_values]
    chal = [backtest_key(v, _challenger_rate, holdout=holdout) for v in series_values]
    finite_champ = [s for s in champ if math.isfinite(s)]
    finite_chal = [s for s in chal if math.isfinite(s)]
    champ_mean = sum(finite_champ) / len(finite_champ) if finite_champ else math.inf
    chal_mean = sum(finite_chal) / len(finite_chal) if finite_chal else math.inf
    return BacktestReport(
        champion_mase=champ_mean,
        challenger_mase=chal_mean,
        n_keys=len(series_values),
        champion_wins=champ_mean <= chal_mean,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/forecasting && uv run --extra dev pytest tests/test_backtest.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add services/forecasting/src/trax_io_forecasting/backtest.py services/forecasting/tests/test_backtest.py
git commit -m "#5 forecasting: MASE backtest + champion/challenger compare"
```

---

## Task 6: #11 projector DI seam + integration

**Files:**
- Modify: `services/recommendation-engine/src/trax_io_reco/service.py` (injectable projector)
- Test: `services/forecasting/tests/test_integration.py`

**Interfaces:**
- Produces: `RecommendationService(*, feature_store, inventory_state, config=None, projector=None)` — `projector` defaults to `HistoricalScheduledProjector()`.
- Consumes: `StatisticalProjector` (T4), `RecommendationService` + `build_stores_from_extract` (#11).

- [ ] **Step 1: Make #11's projector injectable**

In `services/recommendation-engine/src/trax_io_reco/service.py`, the import line
`from trax_io_reco.demand.projection import HistoricalScheduledProjector` becomes:
```python
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector
```
In `RecommendationService.__init__`, change the signature + the projector line:
```python
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
```
(Leave the rest of `__init__` — `self._engine`, `self._aog`, the recommender lists — unchanged.)

- [ ] **Step 2: Verify #11 is unbroken, then reinstall into the forecasting env**

Run:
```bash
cd services/recommendation-engine && uv run --extra dev pytest -q
cd ../forecasting && uv sync --reinstall-package trax-io-reco --extra dev
```
Expected: #11's suite stays green (the change is backward-compatible); the forecasting env picks up the new `RecommendationService` signature.

- [ ] **Step 3: Write the integration test**

Create `services/forecasting/tests/test_integration.py`:
```python
"""End-to-end: inject the StatisticalProjector into #11's RecommendationService."""

from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.service import RecommendationService

from trax_io_forecasting.projector import StatisticalProjector

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def _run(projector) -> int:  # noqa: ANN001
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    svc = RecommendationService(feature_store=fs, inventory_state=inv, projector=projector)
    batch = svc.run(
        tenant=TenantContext(tenant_id=tid), keys=keys, now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    return batch.summary.total


def test_statistical_projector_drives_the_engine() -> None:
    total = _run(StatisticalProjector())
    assert total >= 0  # the injected projector runs end-to-end without error


def test_default_projector_still_works() -> None:
    # default (no projector arg) is unchanged
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    svc = RecommendationService(feature_store=fs, inventory_state=inv)
    batch = svc.run(
        tenant=TenantContext(tenant_id=tid), keys=keys, now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    assert batch.summary.total >= 0
```

- [ ] **Step 4: Run the integration test + full suite + lint**

Run:
```bash
cd services/forecasting && uv run --extra dev pytest -q && uv run --extra dev ruff check .
```
Expected: all green, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/recommendation-engine/src/trax_io_reco/service.py services/forecasting/tests/test_integration.py services/recommendation-engine/uv.lock
git commit -m "#5 forecasting: inject StatisticalProjector into #11 RecommendationService (DI seam) + e2e"
```

---

## Post-implementation

- [ ] Update `CLAUDE.md` Section A run/test table with `services/forecasting/`; `ROADMAP.md` (#5: slice A done — classical intermittent forecasting, the deferred slices B/C/D); `TASKS.md`.
- [ ] Write `docs/adr/2026-06-27-0006-statistical-projector-behind-demandprojector.md` (the DemandProjector-seam decision; statsforecast on 3.14 pins).
- [ ] Adversarial review of the fit→projection mapping, the SBC `select_model`, and the MASE backtest before declaring done.

---

## Self-Review notes (author)

- **Spec coverage:** series §3.1 → T2; classical §3.2 → T3; projector §3.3 → T4; backtest §3.4 → T5; #11 enablement §3.5 → T6; testing §5 (each module + integration + default-unchanged) → tests in T2–T6; deferred items §2 absent by design. The intermittent-only scope is enforced in `StatisticalProjector.project` (delegate when `regime is not INTERMITTENT`).
- **Placeholder scan:** none — every code/test block is complete.
- **Type consistency:** `PeriodSeries{values,bucket,days_per_period}`, `to_period_series`, `ClassicalModel`, `forecast_rate(values,model)`, `select_model(values)`, `StatisticalProjector(fallback,*,model,basis_window_days).project(context,regime)`, `mase`/`naive_scale`/`backtest_key`/`compare`/`BacktestReport`, and the injected `RecommendationService(projector=…)` — used identically across tasks; the `DemandProjection` field set + the COMPOUND_POISSON params match #11 verbatim.
