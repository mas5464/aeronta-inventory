# #5 slice B — Gradient-Boosted Forecasting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GradientBoostedProjector` (sklearn `HistGradientBoostingRegressor`) that forecasts the `MODERATE`/`HIGH_VOLUME` regimes behind #11's `DemandProjectorProtocol`, returning the deterministic `NORMAL` projection with a gradient-boosted mean + residual variance; delegate every other regime to the deterministic fallback.

**Architecture:** A per-key autoregressive model. `features.py` builds lag + rolling features from the gap-filled `PeriodSeries`; `gradient_boosted.py` fits/predicts (lazy sklearn import); `gb_projector.py` assembles the `NORMAL` `DemandProjection`, mirroring `HistoricalScheduledProjector`'s else-branch field-for-field with the GB prediction replacing the historical average. Cold-start (short history) delegates to the fallback.

**Tech Stack:** Python 3.14, scikit-learn (HistGradientBoostingRegressor — self-contained on py3.14), numpy, pydantic v2, uv + pytest + ruff.

## Global Constraints

- **Python ≥3.12, runs on 3.14.** All work in `services/forecasting` (`trax_io_forecasting`). Test: `cd services/forecasting && uv run --extra dev pytest`; lint `uv run --extra dev ruff check .` (ruff line-length 100, select E/F/I/B/UP/N/SIM).
- **Do not modify** slice A's `projector.py`, `series.py`, or `backtest.py`; do not modify `HistoricalScheduledProjector`, the `DemandProjection` contract, the `Regime` enum, or the policy engine.
- **Backend is sklearn `HistGradientBoostingRegressor`** (verified self-contained on py3.14). **Lazy-import sklearn inside `gb_forecast`** (mirroring slice A's lazy statsforecast import), never at module top.
- **Determinism:** every model is constructed with `random_state=0` (or the injected value).
- **The moderate/high projection is `NORMAL`**, mirroring `HistoricalScheduledProjector` exactly: `dist_kind="NORMAL"`, `dist_params={"mean": mean_per_day, "var": var_per_day}`, `var_per_day = max(gb_per_day, residual_var_per_day)`, `mean_per_day = gb_per_day + scheduled_per_day`, `historical_component = gb_per_day`, scheduled/`by_aircraft`/`by_task` computed identically to the deterministic projector. **Only the historical mean's source changes.**
- Reference signatures (verified): `DemandProjectorProtocol.project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection`. `Regime(StrEnum){ULTRA_RARE, INTERMITTENT, MODERATE, HIGH_VOLUME}`. `DemandProjection` fields: `mean_per_day, std_per_day, dist_kind(Literal NORMAL/COMPOUND_POISSON/NBD/EMPIRICAL), dist_params(dict[str,float]), historical_component, scheduled_component, by_aircraft(dict), by_task(dict), basis_window_days`. `to_period_series(history) -> PeriodSeries{values: tuple[float,...], bucket: str, days_per_period: float}`. `HistoricalScheduledProjector(*, basis_window_days=730)`.
- Tests reuse `tests/conftest.py`'s `sample_context` fixture + `with_demand(ctx, obs)` helper. `DemandObservation(bucket: Literal["day","week","month"], period_start: date, removals: int=0, issues: int=0)`.
- Commit after each task with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Autoregressive feature engineering

**Files:**
- Create: `services/forecasting/src/trax_io_forecasting/features.py`
- Test: `services/forecasting/tests/test_features.py`

**Interfaces:**
- Produces:
  - `build_supervised(values: Sequence[float], *, n_lags: int = 6) -> tuple[np.ndarray, np.ndarray]` — `X` shape `(len(values) - n_lags, n_lags + 3)`, `y` shape `(len(values) - n_lags,)`. For each `t` in `[n_lags, len)`: features = `[v[t-1], …, v[t-n_lags]]` (most-recent-first) + `[mean, std, max]` of `v[t-n_lags:t]`; target `v[t]`. Empty arrays of the right column-width when too short.
  - `next_feature_row(values: Sequence[float], *, n_lags: int = 6) -> np.ndarray` — shape `(1, n_lags + 3)`; the row to predict `v[len(values)]` from the last `n_lags` values + their rolling stats. Requires `len(values) >= n_lags`.

- [ ] **Step 1: Write the failing test** — `tests/test_features.py`

```python
import numpy as np

from trax_io_forecasting.features import build_supervised, next_feature_row


def test_build_supervised_shapes_and_lag_order():
    X, y = build_supervised([float(x) for x in range(10)], n_lags=3)
    assert X.shape == (7, 6)  # 10-3 rows; 3 lags + mean/std/max
    assert y.shape == (7,)
    assert list(X[0][:3]) == [2.0, 1.0, 0.0]  # most-recent-first lags before t=3
    assert y[0] == 3.0
    # rolling block on window [0,1,2]: mean=1, max=2
    assert X[0][3] == 1.0
    assert X[0][5] == 2.0


def test_build_supervised_too_short_is_empty_with_right_width():
    X, y = build_supervised([1.0, 2.0], n_lags=6)
    assert X.shape == (0, 9)
    assert y.shape == (0,)


def test_next_feature_row():
    row = next_feature_row([float(x) for x in range(8)], n_lags=3)  # [0..7]
    assert row.shape == (1, 6)
    assert list(row[0][:3]) == [7.0, 6.0, 5.0]  # most-recent-first
    assert np.isfinite(row).all()
```

- [ ] **Step 2: Run it, verify it fails** — `cd services/forecasting && uv run --extra dev pytest tests/test_features.py -v` → FAIL.

- [ ] **Step 3: Implement `features.py`**

```python
"""Per-key autoregressive feature engineering for the gradient-boosted forecaster."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

_ROLLING_COLS = 3  # mean, std, max appended after the lags


def _row(window: list[float]) -> list[float]:
    lags = list(reversed(window))  # [v[t-1], v[t-2], ..., v[t-n_lags]]
    return [*lags, float(np.mean(window)), float(np.std(window)), float(np.max(window))]


def build_supervised(
    values: Sequence[float], *, n_lags: int = 6
) -> tuple[np.ndarray, np.ndarray]:
    vals = [float(v) for v in values]
    rows_x: list[list[float]] = []
    rows_y: list[float] = []
    for t in range(n_lags, len(vals)):
        rows_x.append(_row(vals[t - n_lags : t]))
        rows_y.append(vals[t])
    if not rows_x:
        return np.empty((0, n_lags + _ROLLING_COLS)), np.empty((0,))
    return np.asarray(rows_x, dtype=float), np.asarray(rows_y, dtype=float)


def next_feature_row(values: Sequence[float], *, n_lags: int = 6) -> np.ndarray:
    vals = [float(v) for v in values]
    return np.asarray([_row(vals[-n_lags:])], dtype=float)
```

- [ ] **Step 4: Run tests, verify pass + ruff clean.**

- [ ] **Step 5: Commit** — `git add services/forecasting/src/trax_io_forecasting/features.py services/forecasting/tests/test_features.py && git commit -m "#5 slice B: autoregressive feature engineering (lag + rolling)"`

---

### Task 2: Gradient-boosted forecast core

**Files:**
- Modify: `services/forecasting/pyproject.toml` (add `scikit-learn>=1.6` to `dependencies`)
- Create: `services/forecasting/src/trax_io_forecasting/gradient_boosted.py`
- Test: `services/forecasting/tests/test_gradient_boosted.py`

**Interfaces:**
- Consumes: `features.build_supervised`, `features.next_feature_row`.
- Produces:
  - `gb_forecast(values, *, n_lags=6, min_train_rows=8, random_state=0) -> tuple[float, float] | None` — `None` if `len(values) - n_lags < min_train_rows`; else `(mean_per_period, std_per_period)` where `mean_per_period = max(0.0, predicted next value)` and `std_per_period = std of training residuals`. Lazy-imports sklearn. Deterministic via `random_state`.
  - `gb_next_rate(values, *, n_lags=6, min_train_rows=8, random_state=0) -> float` — the 1-step-ahead prediction, or the historical mean when history is too short. Usable as a `rate_fn` for `backtest_key`.

- [ ] **Step 1: Add the dependency** — in `services/forecasting/pyproject.toml`, add `"scikit-learn>=1.6"` to the `dependencies` array (alongside `numpy`, `statsforecast`, `trax-io-reco`). Run `uv sync --extra dev`.

- [ ] **Step 2: Write the failing test** — `tests/test_gradient_boosted.py`

```python
import math

from trax_io_forecasting.gradient_boosted import gb_forecast, gb_next_rate


def test_gb_forecast_returns_finite_mean_and_std_for_sufficient_history():
    vals = [float(x) for x in ([4, 5, 6, 5] * 6)]  # 24 periods
    fit = gb_forecast(vals)
    assert fit is not None
    mean, std = fit
    assert mean >= 0.0 and std >= 0.0
    assert math.isfinite(mean) and math.isfinite(std)


def test_gb_forecast_none_for_short_history():
    assert gb_forecast([1.0, 2.0, 3.0]) is None  # 3 - 6 < 8


def test_gb_forecast_is_deterministic():
    vals = [float(x % 7) for x in range(30)]
    assert gb_forecast(vals) == gb_forecast(vals)


def test_gb_next_rate_tracks_recent_level_on_ramp():
    ramp = [float(x) for x in range(1, 25)]  # 1..24 ascending
    assert gb_next_rate(ramp) > sum(ramp) / len(ramp)  # above the long-run average


def test_gb_next_rate_falls_back_to_mean_for_short_history():
    assert gb_next_rate([2.0, 4.0]) == 3.0
```

- [ ] **Step 3: Run it, verify it fails.**

- [ ] **Step 4: Implement `gradient_boosted.py`**

```python
"""Gradient-boosted demand forecaster core (sklearn HistGradientBoostingRegressor)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from trax_io_forecasting.features import build_supervised, next_feature_row

_N_LAGS = 6
_MIN_TRAIN_ROWS = 8


def gb_forecast(
    values: Sequence[float],
    *,
    n_lags: int = _N_LAGS,
    min_train_rows: int = _MIN_TRAIN_ROWS,
    random_state: int = 0,
) -> tuple[float, float] | None:
    vals = [float(v) for v in values]
    if len(vals) - n_lags < min_train_rows:
        return None  # caller cold-starts to the deterministic projector

    from sklearn.ensemble import HistGradientBoostingRegressor  # lazy: keep import light

    x, y = build_supervised(vals, n_lags=n_lags)
    model = HistGradientBoostingRegressor(
        max_iter=200, max_depth=3, learning_rate=0.05,
        min_samples_leaf=1, random_state=random_state,
    )
    model.fit(x, y)
    pred = float(model.predict(next_feature_row(vals, n_lags=n_lags))[0])
    mean_per_period = max(0.0, pred)
    std_per_period = float(np.std(y - model.predict(x)))
    return mean_per_period, std_per_period


def gb_next_rate(
    values: Sequence[float],
    *,
    n_lags: int = _N_LAGS,
    min_train_rows: int = _MIN_TRAIN_ROWS,
    random_state: int = 0,
) -> float:
    fit = gb_forecast(
        values, n_lags=n_lags, min_train_rows=min_train_rows, random_state=random_state
    )
    if fit is not None:
        return fit[0]
    vals = [float(v) for v in values]
    return sum(vals) / len(vals) if vals else 0.0
```

- [ ] **Step 5: Run tests, verify pass** — `uv run --extra dev pytest tests/test_gradient_boosted.py -v`. (The first run installs sklearn.) Then ruff clean. If `test_gb_forecast_is_deterministic` ever shows floating-point nondeterminism, that is a real finding — do **not** loosen it without flagging; HGB is deterministic given `random_state`.

- [ ] **Step 6: Commit** — `git commit -m "#5 slice B: gradient-boosted forecast core (sklearn HGB, lazy import)"`

---

### Task 3: GradientBoostedProjector

**Files:**
- Create: `services/forecasting/src/trax_io_forecasting/gb_projector.py`
- Test: `services/forecasting/tests/test_gb_projector.py`

**Interfaces:**
- Consumes: `gb_forecast`, `to_period_series`, `DemandProjection`, `Regime`, `DemandProjectorProtocol`, `HistoricalScheduledProjector`.
- Produces: `GradientBoostedProjector(fallback=None, *, n_lags=6, min_train_rows=8, random_state=0, basis_window_days=730)` implementing `project(*, context, regime) -> DemandProjection`.

- [ ] **Step 1: Write the failing test** — `tests/test_gb_projector.py`

```python
from datetime import date

from trax_io_feature_store.schemas import DemandObservation
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import HistoricalScheduledProjector

from trax_io_forecasting.gb_projector import GradientBoostedProjector

from .conftest import with_demand


def _monthly(values, start_year=2024):
    obs = []
    for i, v in enumerate(values):
        obs.append(
            DemandObservation(
                bucket="month", period_start=date(start_year + i // 12, i % 12 + 1, 1),
                removals=int(v), issues=0,
            )
        )
    return obs


def test_delegates_non_target_regimes(sample_context):
    proj, fb = GradientBoostedProjector(), HistoricalScheduledProjector()
    for regime in (Regime.ULTRA_RARE, Regime.INTERMITTENT):
        assert proj.project(context=sample_context, regime=regime) == fb.project(
            context=sample_context, regime=regime
        )


def test_cold_start_moderate_delegates_to_fallback(sample_context):
    ctx = with_demand(sample_context, _monthly([5, 6, 7]))  # too short to train
    proj, fb = GradientBoostedProjector(), HistoricalScheduledProjector()
    assert proj.project(context=ctx, regime=Regime.MODERATE) == fb.project(
        context=ctx, regime=Regime.MODERATE
    )


def test_moderate_returns_a_normal_projection(sample_context):
    ctx = with_demand(sample_context, _monthly([4, 5, 6, 5] * 6))  # 24 periods
    proj = GradientBoostedProjector().project(context=ctx, regime=Regime.MODERATE)
    assert proj.dist_kind == "NORMAL"
    assert proj.mean_per_day > 0.0
    assert proj.std_per_day > 0.0
    assert set(proj.dist_params) == {"mean", "var"}
    assert proj.historical_component > 0.0


def test_tracks_recent_level_above_average_on_ramp(sample_context):
    ctx = with_demand(sample_context, _monthly(list(range(1, 25))))  # 1..24 ascending
    gb = GradientBoostedProjector().project(context=ctx, regime=Regime.MODERATE)
    det = HistoricalScheduledProjector().project(context=ctx, regime=Regime.MODERATE)
    assert gb.historical_component > det.historical_component
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement `gb_projector.py`**

```python
"""GradientBoostedProjector — a DemandProjector for the MODERATE/HIGH_VOLUME regimes.

Returns the deterministic NORMAL projection (mirroring HistoricalScheduledProjector) with the
historical mean + variance replaced by a gradient-boosted next-period prediction + residual
variance. Every other regime, and any too-short history, delegates to the fallback projector.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector

from trax_io_forecasting.gradient_boosted import gb_forecast
from trax_io_forecasting.series import to_period_series

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

_DEFAULT_BASIS_DAYS = 730
_GB_REGIMES = (Regime.MODERATE, Regime.HIGH_VOLUME)


class GradientBoostedProjector:
    def __init__(
        self,
        fallback: DemandProjectorProtocol | None = None,
        *,
        n_lags: int = 6,
        min_train_rows: int = 8,
        random_state: int = 0,
        basis_window_days: int = _DEFAULT_BASIS_DAYS,
    ) -> None:
        self._fallback = fallback or HistoricalScheduledProjector(
            basis_window_days=basis_window_days
        )
        self._n_lags = n_lags
        self._min_train_rows = min_train_rows
        self._random_state = random_state
        self._basis = basis_window_days

    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection:
        if regime not in _GB_REGIMES:
            return self._fallback.project(context=context, regime=regime)

        series = to_period_series(context.demand_history)
        fit = gb_forecast(
            series.values, n_lags=self._n_lags,
            min_train_rows=self._min_train_rows, random_state=self._random_state,
        )
        if fit is None:  # cold-start: too little history to train
            return self._fallback.project(context=context, regime=regime)

        mean_per_period, std_per_period = fit
        dpp = series.days_per_period or 1.0
        gb_per_day = mean_per_period / dpp
        residual_var_per_day = (std_per_period / dpp) ** 2
        var_per_day = max(gb_per_day, residual_var_per_day)  # same Poisson-ish floor as deterministic

        sched_total = float(sum(s.qty for s in context.scheduled_demand))
        scheduled_per_day = sched_total / self._basis
        by_aircraft: dict[str, float] = {}
        by_task: dict[str, float] = {}
        for s in context.scheduled_demand:
            if s.ac_type:
                by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
            by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty

        mean_per_day = gb_per_day + scheduled_per_day
        return DemandProjection(
            mean_per_day=mean_per_day,
            std_per_day=math.sqrt(var_per_day),
            dist_kind="NORMAL",
            dist_params={"mean": mean_per_day, "var": var_per_day},
            historical_component=gb_per_day,
            scheduled_component=scheduled_per_day,
            by_aircraft=by_aircraft,
            by_task=by_task,
            basis_window_days=self._basis,
        )
```

- [ ] **Step 4: Run tests, verify pass + ruff clean.**

- [ ] **Step 5: Commit** — `git commit -m "#5 slice B: GradientBoostedProjector (NORMAL projection for moderate/high)"`

---

### Task 4: Backtest hook + #11 end-to-end integration

**Files:**
- Test: `services/forecasting/tests/test_gb_backtest.py`
- Test: `services/forecasting/tests/test_gb_integration.py`

**Interfaces:** Consumes the existing `backtest.backtest_key`, `gradient_boosted.gb_next_rate`, `gb_projector.GradientBoostedProjector`, and #11's `RecommendationService` (which already accepts `projector=` since slice A).

- [ ] **Step 1: Write the backtest test** — `tests/test_gb_backtest.py`

```python
import math

from trax_io_forecasting.backtest import backtest_key
from trax_io_forecasting.gradient_boosted import gb_next_rate


def test_gb_next_rate_scores_through_the_holdout_backtest():
    vals = [float(x % 6) for x in range(30)]  # varied -> finite naive scale
    mase = backtest_key(vals, gb_next_rate, holdout=6)
    assert mase >= 0.0
    assert math.isfinite(mase)


def test_gb_champion_vs_historical_mean_challenger_runs():
    vals = [float(x % 5 + 1) for x in range(30)]
    champion = backtest_key(vals, gb_next_rate, holdout=6)
    challenger = backtest_key(vals, lambda v: sum(v) / len(v), holdout=6)
    assert math.isfinite(champion) and math.isfinite(challenger)
```

- [ ] **Step 2: Write the integration test** — `tests/test_gb_integration.py` (mirrors slice A's `test_integration.py`)

```python
from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.service import RecommendationService

from trax_io_forecasting.gb_projector import GradientBoostedProjector

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def test_gradient_boosted_projector_drives_the_engine():
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    svc = RecommendationService(
        feature_store=fs, inventory_state=inv, projector=GradientBoostedProjector()
    )
    batch = svc.run(
        tenant=TenantContext(tenant_id=tid), keys=keys, now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    assert batch.summary.total >= 0  # the injected projector runs end to end without error
```

- [ ] **Step 3: Run both tests, verify pass** — `uv run --extra dev pytest tests/test_gb_backtest.py tests/test_gb_integration.py -v`.

- [ ] **Step 4: Run the full forecasting suite** to confirm no regression — `uv run --extra dev pytest -q` (slice A's tests + the new slice B tests). ruff clean.

- [ ] **Step 5: Commit** — `git commit -m "#5 slice B: backtest hook + #11 end-to-end integration"`

---

## Post-implementation (controller, after final review)

- ADR `docs/adr/2026-06-27-0009-gradient-boosted-projector.md` (GB projector behind the DemandProjector seam; sklearn-HGB backend with LightGBM/libomp deferred; per-key AR scope, causal/global/SageMaker/Chronos/45-day-gate deferred).
- CLAUDE.md: note forecasting now covers intermittent (slice A) + moderate/high (slice B).
- ROADMAP #5: mark slice B done; keep slices C/D deferred; note the LightGBM-backend + causal-covariate deferrals.
- TASKS.md session entry. Merge `feat/forecasting-gradient-boosted` → main, push, delete branch (restore any unrelated lockfile churn first).

## Self-Review

- **Spec coverage:** §4.1 features → Task 1; §4.2 gb core → Task 2; §4.3 projector → Task 3; §5 backtest + integration → Task 4. All covered.
- **Type consistency:** `gb_forecast` / `gb_next_rate` signatures match between Tasks 2–4. `GradientBoostedProjector` ctor + `project` match the spec and the verified `DemandProjectorProtocol`. The NORMAL assembly matches `HistoricalScheduledProjector`'s field set exactly (verified against `projection.py`).
- **Placeholders:** none — every step has runnable code. The trend-tracking assertion (`gb.historical_component > det.historical_component`) is robust on a ramp even in HGB's degenerate no-split case (target-mean ≈ 15.5 ⇒ 0.509/day > the long-run average 300/730 ≈ 0.411/day).
