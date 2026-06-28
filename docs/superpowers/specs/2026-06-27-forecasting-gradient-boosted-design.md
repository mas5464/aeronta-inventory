# #5 slice B — Gradient-Boosted Forecasting (moderate / high-volume) — Design

**Date:** 2026-06-27
**Status:** Proposed
**Sub-project:** #5 Forecasting & Policy Engine (slice B)
**Authoritative inputs:**
[design §5.1–5.2 regimes/models](../../design/2026-04-14-trax-io-inventory-optimizer-design.md) ·
[#5 sub-plan](../../plans/2026-04-14-forecasting-policy-plan.md) ·
[ADR-0006 slice A / DemandProjector seam](../../adr/2026-06-27-0006-statistical-projector-behind-demandprojector.md)

## 1. Context

Slice A shipped intermittent-regime forecasting (`StatisticalProjector`, Croston/SBA/TSB) behind #11's `DemandProjectorProtocol`, delegating every other regime to the deterministic `HistoricalScheduledProjector`. Slice B fills the **`MODERATE`** (25–200 events/24mo) and **`HIGH_VOLUME`** (201+) regimes — where single-series signal is strongest and most demand volume lives — with a gradient-boosted forecaster behind the same seam.

Three facts from grounding shape the slice:

1. **The projection contract for moderate/high is `NORMAL`** (not the intermittent `COMPOUND_POISSON`). The deterministic projector fits it by method-of-moments: `mean_per_day = historical_per_day + scheduled_per_day`, `dist_params={"mean", "var"}`, `var_per_day = max(historical_per_day, var_of_daily_rates)`. Slice B reuses this shape **field-for-field**, changing only the source of the historical mean (and its variance) — a gradient-boosted next-period prediction + residual variance instead of the raw average. Same philosophy as slice A.
2. **The design's LightGBM is causal-covariate-rich** (flight hours, cycles, wash rate) — but `causal_utilization` is **stubbed to `None` in v1**, and static covariates are constant within a single key (no predictive value to a per-key model). The honest, locally-trainable contribution is a **per-key autoregressive** gradient booster on the key's own demand series. The global causal-covariate / federated model is deferred until `causal_utilization` is wired and a real multi-key corpus exists.
3. **The backend is scikit-learn's `HistGradientBoostingRegressor`** (decision below), a histogram gradient-boosted tree of the same family as LightGBM, self-contained on Python 3.14.

### Backend decision (probed)

LightGBM (the design's named tool) ships a py3.14 wheel but its native lib **fails to load without a system `libomp.dylib`** (`brew install libomp`) — not self-contained, every dev/CI box would need it. sklearn `HistGradientBoostingRegressor` installs + fits cleanly on py3.14 with **zero system dependency** (verified, sklearn 1.8.0). **Decision:** sklearn HGB is the backend; LightGBM is recorded in the ADR as a deferred optional backend (swappable once libomp is available). This is the slice-A `statsforecast`-vs-numpy fork — except here the design's tool is not cleanly viable, so the self-contained equivalent wins.

## 2. Scope

**In scope (locally verifiable):**
1. `GradientBoostedProjector` (`DemandProjectorProtocol`) — handles `MODERATE` + `HIGH_VOLUME`, delegates `ULTRA_RARE`/`INTERMITTENT` to an injected fallback (default `HistoricalScheduledProjector`).
2. Per-key autoregressive **feature engineering** (lag + rolling features from the gap-filled `PeriodSeries`).
3. A `gb_forecast` core — fit `HistGradientBoostingRegressor` on the supervised set → next-period mean + residual std; returns `None` when history is too short.
4. **NORMAL `DemandProjection`** assembly mirroring the deterministic projector (scheduled component / `by_aircraft` / `by_task` reused verbatim; GB-predicted mean + residual variance).
5. **Cold-start fallback** — short history delegates to the deterministic projector.
6. **Backtest hook** — a `gb_next_rate` champion usable with the existing `backtest_key`/MASE harness vs the historical-average challenger.
7. End-to-end integration: `GradientBoostedProjector` injected into #11's `RecommendationService`.

**Deferred (tracked in ROADMAP):** causal covariates (flight hours/cycles/wash rate — `causal_utilization` is stubbed); the global cross-sectional / federated cross-tenant model; the LightGBM backend (needs libomp); SageMaker hosting + real-time inference; the foundation-model (Chronos) ensemble for high_volume; the nightly champion/challenger auto-promotion gate (45-day) — that's slice D.

**Non-goals:** changing `HistoricalScheduledProjector`, the `DemandProjection` contract, the regime classifier, or the policy engine; quantile/interval forecasts beyond mean+variance.

## 3. Package & dependency layout

All work in `services/forecasting` (`trax_io_forecasting`). New modules:

```
src/trax_io_forecasting/
  features.py        # build_supervised(values, *, n_lags) -> (X, y); next_feature_row(values, *, n_lags)
  gradient_boosted.py# gb_forecast(values, ...) -> (mean_per_period, std_per_period) | None; gb_next_rate(values) -> float
  gb_projector.py    # GradientBoostedProjector(DemandProjectorProtocol)
```

`pyproject.toml`: add `"scikit-learn>=1.6"` to `dependencies` (numpy already present). sklearn is **lazy-imported inside `gb_forecast`** (mirroring slice A's lazy statsforecast import) so package import stays light. No change to `projector.py` (slice A), `series.py`, or `backtest.py`.

## 4. Components

### 4.1 Feature engineering (`features.py`)

From the dense, gap-filled `PeriodSeries.values` (built by the existing `to_period_series`), build a per-key autoregressive supervised set:

- `build_supervised(values, *, n_lags=6) -> (X: np.ndarray, y: np.ndarray)` — for each `t` in `[n_lags, len(values))`: features = the `n_lags` lagged values `[v[t-1], …, v[t-n_lags]]` plus rolling stats over `v[t-n_lags:t]` (`mean`, `std`, `max`); target = `v[t]`.
- `next_feature_row(values, *, n_lags=6) -> np.ndarray` — the single feature row to predict `v[len(values)]` from the last `n_lags` values + their rolling stats.

### 4.2 GB core (`gradient_boosted.py`)

- `gb_forecast(values, *, n_lags=6, min_train_rows=8, random_state=0) -> tuple[float, float] | None`:
  - returns `None` if `len(values) - n_lags < min_train_rows` (insufficient history → caller cold-starts);
  - else `build_supervised`, fit `HistGradientBoostingRegressor(random_state=random_state, …)`, predict `next_feature_row` → `mean_per_period = max(0.0, pred)`; residuals on the training rows → `std_per_period = float(np.std(y_train - model.predict(X_train)))`; return `(mean_per_period, std_per_period)`. Deterministic via `random_state`.
- `gb_next_rate(values) -> float` — the 1-step-ahead prediction (or the historical mean when history is too short), usable as a champion `rate_fn` with the existing `backtest_key(values, rate_fn, *, holdout)`.

### 4.3 Projector (`gb_projector.py`)

```python
class GradientBoostedProjector:
    def __init__(self, fallback=None, *, n_lags=6, min_train_rows=8,
                 random_state=0, basis_window_days=730): ...
    def project(self, *, context, regime) -> DemandProjection: ...
```

`project`:
1. if `regime not in (MODERATE, HIGH_VOLUME)` → `self._fallback.project(...)`.
2. `series = to_period_series(context.demand_history)`; `fit = gb_forecast(series.values, …)`.
3. if `fit is None` (cold-start) → `self._fallback.project(...)`.
4. else build the **NORMAL** projection mirroring `HistoricalScheduledProjector`'s else-branch, with `gb_per_day = mean_per_period / series.days_per_period` replacing `historical_per_day`:
   - `residual_var_per_day = (std_per_period / days_per_period) ** 2`
   - `var_per_day = max(gb_per_day, residual_var_per_day)`  *(same Poisson-ish floor as the deterministic `max(historical_per_day, r_var)`)*
   - `scheduled_per_day`, `by_aircraft`, `by_task` computed **identically** to the deterministic/slice-A projectors
   - `mean_per_day = gb_per_day + scheduled_per_day`, `std_per_day = sqrt(var_per_day)`, `dist_kind="NORMAL"`, `dist_params={"mean": mean_per_day, "var": var_per_day}`, `historical_component=gb_per_day`, `scheduled_component=scheduled_per_day`, `basis_window_days=self._basis`.

So a moderate/high projection is structurally indistinguishable from the deterministic one to the policy engine — only the mean (and its variance) is better.

## 5. Testing strategy

- **features** — `build_supervised` row/column shapes (`n_rows = len - n_lags`, `n_cols = n_lags + 3`), lag ordering, rolling values; `next_feature_row` shape + values.
- **gb_forecast** — returns a finite `(mean ≥ 0, std ≥ 0)` for a sufficient series; `None` for a short series; **deterministic** (same input → same output via `random_state`); on a monotone ramp the predicted next value tracks the recent level (well above the long-run average).
- **projector** —
  - **delegation:** `ULTRA_RARE` and `INTERMITTENT` regimes return a projection **equal** to the fallback's (identity);
  - **cold-start:** a `MODERATE` context with short history returns the fallback's projection;
  - **NORMAL correctness:** a `MODERATE` context with sufficient history → `dist_kind=="NORMAL"`, finite `mean_per_day>0`, `std_per_day>0`, `dist_params` has `mean`+`var`, scheduled components reused;
  - **trend tracking:** on a ramped demand history, `GradientBoostedProjector.historical_component > HistoricalScheduledProjector.historical_component` (GB tracks the recent level above the long-run average).
- **backtest** — `gb_next_rate` runs through `backtest_key(values, gb_next_rate, holdout=6)` returning a finite MASE on a learnable series; a champion-vs-challenger comparison (GB vs historical mean) completes.
- **integration** — `GradientBoostedProjector()` injected into #11's `RecommendationService` runs end to end over the extract sample (`total ≥ 0`), and the default (no projector) path is unchanged.

Tests use the existing `tests/conftest.py` `sample_context` fixture + `with_demand(ctx, obs)` helper to inject synthetic monthly histories (ramp / flat / short) and pass `regime=Regime.MODERATE` explicitly (independent of the classifier).

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Trees can't extrapolate beyond seen targets | Acceptable: tracking the recent **level** (not unbounded trend) is the goal; the trend-tracking test asserts `> long-run average`, which holds for a ramp. |
| HGB non-determinism across runs | `random_state=0` fixed; determinism asserted in a test. |
| sklearn import cost at package load | Lazy-imported inside `gb_forecast`, like slice A's statsforecast. |
| Short aerospace histories overfit | `min_train_rows` cold-start guard delegates to the deterministic projector; threshold is configurable. |
| Over-claiming causal covariates | Explicitly per-key autoregressive only; causal-covariate/global model deferred and documented (causal_utilization is stubbed in v1). |

## 7. Deliverables

- `features.py`, `gradient_boosted.py`, `gb_projector.py` with full tests; `scikit-learn>=1.6` dep; ruff-clean.
- ADR-0009 (gradient-boosted projector behind the DemandProjector seam; sklearn-HGB backend with LightGBM/libomp deferred; per-key AR scope with causal/global deferred).
- CLAUDE.md note (forecasting now covers intermittent + moderate/high); ROADMAP #5 slice B done + deferrals; TASKS.md.
